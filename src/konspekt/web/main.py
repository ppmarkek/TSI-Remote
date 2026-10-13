"""Start the HTML interface in a native pywebview window."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

from ..diagnostics import record_exception

WINDOW_TITLE = "Конспект"
# Caption colours follow the sidebar of the light and dark themes in styles.css.
CHROME = {
    False: {"background": "#F6F7F9", "caption": "#EEF0F3", "text": "#5A6170", "border": "#E4E7EC"},
    True: {"background": "#15171C", "caption": "#1B1E24", "text": "#A3A9B6", "border": "#2E333D"},
}
SMOKE_TIMEOUT_SECONDS = 12.0


def static_directory() -> Path:
    return Path(__file__).resolve().parent / "static"


def run_web_app(argv: list[str]) -> bool:
    """Run the HTML interface; return False when it cannot start so Tk can take over."""

    try:
        import webview
    except ImportError:
        return False
    if not (static_directory() / "index.html").is_file():
        return False

    from ..platform_services import webview_gui_for_platform
    from ..window_chrome import system_prefers_dark
    from .api import KonspektApi
    from .server import LocalServer

    smoke_test = "--smoke-test-gui" in argv
    api = KonspektApi(startup_warning=_migrate_legacy_data())
    server = LocalServer(static_directory(), api._media_path)
    try:
        base_url = server.start()
    except OSError as exc:
        record_exception("web.server.start", exc)
        return False

    dark = system_prefers_dark()
    preferred_theme = api._study.preferences().get("theme")
    if preferred_theme in {"light", "dark"}:
        dark = preferred_theme == "dark"
    chrome = CHROME[dark]
    window = webview.create_window(
        WINDOW_TITLE,
        url=base_url + "index.html",
        js_api=api,
        width=1280,
        height=820,
        min_size=(960, 620),
        background_color=chrome["background"],
        text_select=True,
        localization={
            "global.ok": "Закрыть",
            "global.cancel": "Отмена",
            "global.quit": "Выйти",
            "global.quitConfirmation": "Закрыть приложение?",
            "global.saveFile": "Сохранить файл",
            "windows.fileFilter.allFiles": "Все файлы",
            "windows.fileFilter.otherFiles": "Другие файлы",
        },
    )
    if window is None:
        server.stop()
        return False
    api._attach_window(window)
    api._set_chrome_handler(lambda is_dark: _style_window(window, is_dark))

    shown = threading.Event()

    def on_shown() -> None:
        shown.set()
        _style_window(window, dark)

    def on_closing() -> bool:
        if not api._has_active_tasks() or smoke_test:
            return True
        return bool(
            window.create_confirmation_dialog(
                "Идёт обработка",
                "Лекция ещё обрабатывается. Закрыть приложение и остановить обработку?",
            )
        )

    window.events.shown += on_shown
    window.events.closing += on_closing

    smoke_result: dict[str, Any] = {"passed": False}
    start_kwargs: dict[str, Any] = {
        "gui": webview_gui_for_platform(),
        "debug": "--devtools" in argv,
    }
    if smoke_test:
        start_kwargs["func"] = _smoke_check
        start_kwargs["args"] = (window, smoke_result)
    try:
        webview.start(**start_kwargs)
    except Exception as exc:
        record_exception("web.start", exc)
        # Without a working WebView (for example, no WebView2 runtime) the
        # classic interface still lets the user study.
        if not shown.is_set():
            return False
    finally:
        api._shutdown()
        server.stop()

    if smoke_test:
        if not smoke_result["passed"]:
            sys.stderr.write(f"Web GUI smoke test failed: {smoke_result.get('error', '')}\n")
            raise SystemExit(1)
        sys.stdout.write("GUI smoke test passed\n")
    return True


def _style_window(window: Any, dark: bool) -> None:
    if sys.platform != "win32":
        return
    from ..window_chrome import style_native_hwnd

    chrome = CHROME[bool(dark)]
    try:
        hwnd = int(window.native.Handle.ToInt64())
    except Exception:
        return
    style_native_hwnd(
        hwnd,
        caption=chrome["caption"],
        text=chrome["text"],
        border=chrome["border"],
        dark=bool(dark),
    )


def _smoke_check(window: Any, result: dict[str, Any]) -> None:
    """Wait until the page has booted and talked to Python, then close the window."""

    deadline = threading.Event()
    try:
        if not window.events.loaded.wait(SMOKE_TIMEOUT_SECONDS):
            result["error"] = "page did not load"
            return
        for _ in range(int(SMOKE_TIMEOUT_SECONDS * 4)):
            state = window.evaluate_js("window.__konspektReady === true")
            if state is True:
                result["passed"] = True
                return
            deadline.wait(0.25)
        result["error"] = "interface did not report readiness"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        window.destroy()


def _migrate_legacy_data() -> str:
    from ..platform_services import MigrationStatus, PlatformAppPaths, migrate_legacy_data

    try:
        migration = migrate_legacy_data(PlatformAppPaths())
    except Exception as exc:
        record_exception("migration.startup", exc)
        return "Не удалось проверить перенос старых данных."
    if migration.status in {MigrationStatus.CONFLICT, MigrationStatus.ERROR}:
        return migration.error_message or "Не удалось безопасно перенести старые данные."
    return ""
