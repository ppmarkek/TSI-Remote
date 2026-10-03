"""Capture the real desktop UI with isolated example data and check navigation.

Run from the repository's development environment on Windows. Example records
and lesson content live only in a temporary directory, never the user's library.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from PIL import Image, ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def capture_window(hwnd: int) -> Image.Image:
    """Capture the compositor's visible frame, including the DWM caption."""
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    visible = wintypes.RECT()
    assert dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(visible), ctypes.sizeof(visible)) == 0
    return ImageGrab.grab(
        bbox=(visible.left, visible.top, visible.right, visible.bottom),
        all_screens=True,
    )


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("This preview captures native Windows frames.")
    from konspekt.app import PALETTE, StudyApp
    from konspekt.bbb_import import BBBRecording
    from konspekt.library_manager import save_library
    from konspekt.local_pipeline import default_lecture_directory

    output = ROOT / "build" / "ui-preview"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="konspekt-ui-preview-") as directory:
        isolated = Path(directory)
        environment = {
            f"KONSPEKT_{name}_DIR": str(isolated / name.lower())
            for name in ("DATA", "CACHE", "LOG", "TEMP")
        }
        records = [
            BBBRecording(
                meeting_id="preview-ready",
                source_url="https://example.test/playback?meetingId=preview-ready",
                title="Virtual Classroom",
                imported_at="2026-07-15T13:40:00+00:00",
                audio_video_url="https://example.test/audio.mp4",
                screen_video_url=None,
                slides=(),
            ),
            BBBRecording(
                meeting_id="preview-pending",
                source_url="https://example.test/playback?meetingId=preview-pending",
                title="Virtual Classroom (4501BTA)",
                imported_at="2026-07-15T13:22:00+00:00",
                audio_video_url="https://example.test/audio.mp4",
                screen_video_url=None,
                slides=(),
            ),
        ]
        with patch.dict(os.environ, environment):
            data = isolated / "data"
            data.mkdir()
            save_library(records, path=data / "library.json")
            lesson_directory = default_lecture_directory(records[0], base_dir=data)
            lesson_directory.mkdir(parents=True)
            (lesson_directory / "lesson.md").write_text(
                "# Пример конспекта\n\nМатериал для проверки оформления.\n", encoding="utf-8"
            )
            app = StudyApp()
            errors: list[str] = []
            app.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
            # The settings preview must not initiate account/network inspection.
            app._refresh_chatgpt_account = lambda: None

            def settle() -> None:
                until = time.monotonic() + 0.25
                while time.monotonic() < until:
                    app.update()
                    time.sleep(0.01)

            def widgets() -> list[tk.Misc]:
                pending = [app]
                result = []
                while pending:
                    current = pending.pop()
                    result.append(current)
                    pending.extend(current.winfo_children())
                return result

            def text_present(value: str) -> bool:
                return any(
                    isinstance(widget, tk.Label) and widget.cget("text") == value
                    for widget in widgets()
                )

            def capture(name: str) -> None:
                print(f"Capturing {name}...", flush=True)
                settle()
                user32 = ctypes.WinDLL("user32")
                user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
                user32.GetAncestor.restype = wintypes.HWND
                focused = app.focus_get()
                app.attributes("-topmost", True)
                app.lift()
                if focused is not None:
                    focused.focus_force()
                else:
                    app.focus_force()
                settle()
                try:
                    hwnd = user32.GetAncestor(app.winfo_id(), 2)
                    frame = capture_window(hwnd)
                    if sys.getwindowsversion().build >= 22000:
                        expected = tuple(int(PALETTE["sidebar"][i : i + 2], 16) for i in (1, 3, 5))
                        assert frame.getpixel((frame.width // 2, 12)) == expected, (
                            name,
                            "native caption colour not applied",
                        )
                    frame.save(output / f"{name}.png")
                finally:
                    app.attributes("-topmost", False)
                for widget in widgets():
                    if isinstance(widget, ttk.Button) and widget.winfo_viewable():
                        assert widget.winfo_width() >= widget.winfo_reqwidth(), (
                            name,
                            widget.cget("text"),
                            "button clipped",
                        )
                        assert widget.winfo_height() >= widget.winfo_reqheight(), (
                            name,
                            widget.cget("text"),
                            "button height clipped",
                            widget.winfo_height(),
                            widget.winfo_reqheight(),
                        )

            try:
                app.geometry("1240x780+80+60")
                app.show_library(animated=False)
                capture("library-desktop")
                assert app._nav_buttons["library"].instate(["selected"])
                app._nav_buttons["library"].focus_set()
                capture("library-focus")
                assert app._nav_buttons["library"].instate(["selected", "focus"])
                app.focus_set()

                app._library_query.set("no matching lecture")
                app.show_library(animated=False)
                settle()
                assert text_present("Лекции не найдены")
                capture("library-no-results")
                app._library_query.set("")

                app.geometry("960x660+80+60")
                app.show_library(animated=False)
                capture("library-compact")
                app.library = []
                app.show_library(animated=False)
                capture("library-empty")
                app.library = records

                app.geometry("1240x780+80+60")
                app.show_new_lecture()
                capture("import-desktop")
                assert all(button.instate(["!selected"]) for button in app._nav_buttons.values())
                app.show_settings()
                capture("settings-desktop")
                assert app._nav_buttons["settings"].instate(["selected"])
                assert not errors, errors
                print("UI checks passed: navigation, search, empty states, widths, native caption.")
                print(f"Example-data screenshots: {output}")
            finally:
                app._on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
