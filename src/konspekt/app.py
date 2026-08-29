#!/usr/bin/env python3
"""Quiet archive desktop interface for importing and preparing lecture materials."""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import font, scrolledtext, ttk

from .api_generation import (
    ApiGenerationError,
    ApiLessonResult,
    generate_lesson_via_api,
)
from .bbb_import import (
    BBBImportError,
    BBBRecording,
    inspect_bbb_recording,
)
from .chatgpt_account import (
    ChatGPTAccountError,
    ChatGPTAccountStatus,
    ChatGPTGenerationResult,
    ChatGPTModel,
    chatgpt_account_status,
    generate_lesson_with_chatgpt,
    list_chatgpt_models,
    login_with_chatgpt,
)
from .context_package import (
    ContextPackage,
    ContextPackageError,
    build_context_package,
    context_package_is_ready,
)
from .deepseek_handoff import (
    DeepSeekHandoff,
    DeepSeekHandoffError,
    launch_deepseek_handoff,
    prepare_deepseek_handoff,
)
from .diagnostics import record_exception
from .job_runner import CancellationToken, JobEvent, JobEventType, JobRunner
from .lesson_output import (
    LessonOutputError,
    lesson_is_ready,
    read_generated_lesson,
    save_generated_lesson,
)
from .library_manager import (
    calculate_library_size,
    empty_trash,
    filter_and_sort_recordings,
    format_imported_at,
    list_trash,
    load_library,
    move_to_trash,
    rename_recording,
    restore_from_trash,
    save_to_library,
)
from .local_pipeline import (
    LocalProcessingError,
    default_lecture_directory,
    lecture_is_prepared,
    prepare_lecture,
)
from .markdown_reader import extract_table_of_contents, extract_timestamps
from .outbound_context import (
    OutboundContextError,
    _validate_outbound_text,
    validate_provider_context_limits,
)
from .settings import (
    AppSettings,
    SettingsError,
    default_model_for_provider,
    load_settings,
    save_settings,
)

PALETTE = {
    "canvas": "#F4F7F7",
    "sidebar": "#20292B",
    "surface": "#FFFFFF",
    "surface_soft": "#EAF0F0",
    "ink": "#172124",
    "muted": "#4C5C60",
    "faint": "#718084",
    "line": "#C9D4D6",
    "primary": "#0A6570",
    "primary_hover": "#07535D",
    "primary_pressed": "#063F47",
    "primary_soft": "#DDECEE",
    "focus": "#A2482E",
    "success": "#2E765A",
    "danger": "#A54242",
    "sidebar_ink": "#EAF0F0",
    "sidebar_muted": "#8A9B9E",
    "sidebar_active": "#2B373A",
    "sidebar_hover": "#263133",
    "sidebar_line": "#2C383A",
}


def _operation_is_current(app: object, operation_id: int | None) -> bool:
    """Let legacy worker tests run while rejecting callbacks from stale jobs."""

    return operation_id is None or operation_id == getattr(
        app,
        "_processing_operation_id",
        None,
    )


def _deliver_processing_progress(
    app: object,
    operation_id: int | None,
    percent: int,
    message: str,
) -> None:
    if _operation_is_current(app, operation_id):
        app._set_processing_progress(percent, message)  # type: ignore[attr-defined]


def _deliver_processing_error(
    app: object,
    operation_id: int | None,
    message: str,
    diagnostic_path: Path | None,
) -> None:
    if not _operation_is_current(app, operation_id):
        return
    if hasattr(app, "_processing_diagnostic_path"):
        app._processing_diagnostic_path = diagnostic_path  # type: ignore[attr-defined]
    app._finish_processing_error(message)  # type: ignore[attr-defined]


def _deliver_processing_result(
    app: object,
    operation_id: int | None,
    callback,
    *args: object,
) -> None:
    if _operation_is_current(app, operation_id):
        callback(*args)


def asset_path(name: str) -> Path:
    """Locate a bundled asset both from source and from a PyInstaller build."""

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / name


@dataclass(frozen=True)
class Typography:
    family: str
    title: tuple[str, int, str]
    heading: tuple[str, int, str]
    subheading: tuple[str, int, str]
    body: tuple[str, int]
    body_bold: tuple[str, int, str]
    secondary: tuple[str, int]
    small: tuple[str, int]


class StudyApp(tk.Tk):
    """A quiet archive desktop study workspace."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Конспект — учебные материалы")
        self.geometry("1180x760")
        self.minsize(980, 660)
        self.configure(background=PALETTE["canvas"])

        self.type = self._create_typography()
        self._app_icon = self._load_app_icon()
        self._sidebar_icon = self._app_icon.subsample(8, 8) if self._app_icon else None
        if self._app_icon is not None:
            self.iconphoto(True, self._app_icon)
        self.style = ttk.Style(self)
        self._configure_styles()
        self._current_screen: ttk.Frame | None = None
        from .platform_services import MigrationStatus, PlatformAppPaths, migrate_legacy_data

        self.app_paths = PlatformAppPaths()
        migration_warning = ""
        try:
            migration = migrate_legacy_data(self.app_paths)
            if migration.status in {MigrationStatus.CONFLICT, MigrationStatus.ERROR}:
                migration_warning = migration.error_message or (
                    "Не удалось безопасно перенести старые данные."
                )
        except Exception as exc:
            migration_warning = "Не удалось проверить перенос старых данных."
            record_exception("migration.startup", exc)
        self.settings, settings_warning = self._load_settings_safely()
        self._settings_load_warning = " ".join(
            item for item in (migration_warning, settings_warning) if item
        )
        self.library: list[BBBRecording] = self._load_library_safely()
        self._library_query = tk.StringVar()
        self._library_state_filter = tk.StringVar(value="Все состояния")
        self._library_date_filter = tk.StringVar(value="Все даты")
        self._library_sort = tk.StringVar(value="Сначала новые")
        self._library_storage_status = tk.StringVar()
        self._reading_positions: dict[str, float] = {}
        self._bbb_url = tk.StringVar()
        self._import_status = tk.StringVar()
        self._import_button: ttk.Button | None = None
        self._import_status_label: tk.Label | None = None
        self._processing_status = tk.StringVar()
        self._processing_percent = tk.StringVar(value="0%")
        self._processing_state = tk.StringVar(value="Выполняется")
        self._processing_activity = tk.StringVar()
        self._processing_diagnostic = tk.StringVar()
        self._processing_progress: ttk.Progressbar | None = None
        self._processing_status_label: tk.Label | None = None
        self._processing_percent_label: tk.Label | None = None
        self._processing_return_button: ttk.Button | None = None
        self._processing_retry_button: ttk.Button | None = None
        self._processing_started_at = 0.0
        self._processing_last_activity_at = 0.0
        self._processing_active = False
        self._processing_heartbeat_id = 0
        self._processing_operation_id = 0
        self._processing_diagnostic_path: Path | None = None
        self._job_runner = JobRunner()
        self._processing_token: CancellationToken | None = None
        self._processing_cancel_button: ttk.Button | None = None
        self._active_processing_recording: BBBRecording | None = None
        self._active_processing_kind: str | None = None
        self._handoff_status = tk.StringVar()
        self._active_handoff: DeepSeekHandoff | None = None
        self._active_handoff_provider: str | None = None
        self._lesson_status = tk.StringVar()
        self._lesson_editor: scrolledtext.ScrolledText | None = None
        self._settings_provider = tk.StringVar(value=self.settings.api_provider)
        self._settings_api_key = tk.StringVar(value=self.settings.api_key)
        self._settings_api_model = tk.StringVar(value=self.settings.api_model)
        self._settings_chatgpt_model = tk.StringVar(value=self.settings.chatgpt_model)
        self._settings_whisper_model = tk.StringVar(value=self.settings.whisper_model)
        self._settings_frame_interval = tk.StringVar(
            value=str(self.settings.frame_interval_seconds)
        )
        self._settings_ocr_enabled = tk.BooleanVar(value=self.settings.ocr_enabled)
        self._settings_status = tk.StringVar(value=self._settings_load_warning)
        self._settings_status_label: tk.Label | None = None
        self._chatgpt_status = tk.StringVar(value="Проверяем состояние входа…")
        self._chatgpt_generation_action = tk.StringVar(value="Создать через ChatGPT")
        self._chatgpt_model_summary = tk.StringVar(
            value=(
                "API-ключ не нужен; используется лимит Codex твоего тарифа. "
                "Конспект сохранится автоматически."
            )
        )
        self._chatgpt_account: ChatGPTAccountStatus | None = None
        self._chatgpt_account_operation_id = 0
        self._chatgpt_login_active = False
        self._chatgpt_status_label: tk.Label | None = None
        self._chatgpt_login_button: ttk.Button | None = None
        self._chatgpt_model_combobox: ttk.Combobox | None = None
        self._navigation_buttons: list[ttk.Button] = []

        self._build_shell()
        self.show_library(animated=False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        """Cancel active work and wait briefly before closing the Tk shell."""
        self._job_runner.shutdown(timeout_seconds=3.0)
        self.destroy()

    @staticmethod
    def _load_app_icon() -> tk.PhotoImage | None:
        try:
            return tk.PhotoImage(file=asset_path("konspekt.png"))
        except tk.TclError:
            return None

    def _create_typography(self) -> Typography:
        from .platform_services import PlatformAppearancePreferences

        available = set(font.families())
        pref = PlatformAppearancePreferences().font_family_body
        if sys.platform == "darwin" and "Helvetica Neue" in available:
            family = "Helvetica Neue"
        elif "Segoe UI Variable Text" in available:
            family = "Segoe UI Variable Text"
        elif "Segoe UI Variable" in available:
            family = "Segoe UI Variable"
        elif pref in available:
            family = pref
        elif "Helvetica Neue" in available:
            family = "Helvetica Neue"
        elif "Segoe UI" in available:
            family = "Segoe UI"
        elif "Arial" in available:
            family = "Arial"
        else:
            family = "TkDefaultFont"
        return Typography(
            family=family,
            title=(family, 22, "bold"),
            heading=(family, 14, "bold"),
            subheading=(family, 11, "bold"),
            body=(family, 11),
            body_bold=(family, 11, "bold"),
            secondary=(family, 10),
            small=(family, 10),
        )

    def _configure_styles(self) -> None:
        self.style.theme_use("clam")
        self.style.configure("TFrame", background=PALETTE["canvas"])

        self.style.configure(
            "Primary.TButton",
            background=PALETTE["primary"],
            foreground="#FFFFFF",
            borderwidth=0,
            focuscolor=PALETTE["focus"],
            font=self.type.body_bold,
            padding=(16, 9),
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#BDCED0"),
                ("pressed", PALETTE["primary_pressed"]),
                ("active", PALETTE["primary_hover"]),
            ],
            foreground=[("disabled", "#7E9698")],
        )

        self.style.configure(
            "Secondary.TButton",
            background=PALETTE["surface"],
            foreground=PALETTE["ink"],
            borderwidth=1,
            relief="solid",
            bordercolor=PALETTE["line"],
            focuscolor=PALETTE["focus"],
            font=self.type.body_bold,
            padding=(14, 8),
        )
        self.style.map(
            "Secondary.TButton",
            background=[
                ("disabled", PALETTE["canvas"]),
                ("active", PALETTE["surface_soft"]),
            ],
            foreground=[("disabled", PALETTE["faint"])],
            bordercolor=[("disabled", PALETTE["line"])],
        )

        self.style.configure(
            "Nav.TButton",
            background=PALETTE["sidebar"],
            foreground=PALETTE["sidebar_ink"],
            borderwidth=0,
            font=self.type.body,
            anchor="w",
            padding=(12, 9),
        )
        self.style.map(
            "Nav.TButton",
            background=[
                ("selected", PALETTE["sidebar_active"]),
                ("active", PALETTE["sidebar_hover"]),
            ],
            foreground=[("active", "#FFFFFF"), ("disabled", PALETTE["sidebar_muted"])],
        )

        self.style.configure(
            "SidebarPrimary.TButton",
            background=PALETTE["primary"],
            foreground="#FFFFFF",
            borderwidth=0,
            focuscolor=PALETTE["focus"],
            font=self.type.body_bold,
            anchor="w",
            padding=(12, 9),
        )
        self.style.map(
            "SidebarPrimary.TButton",
            background=[
                ("disabled", "#2C383A"),
                ("pressed", PALETTE["primary_pressed"]),
                ("active", PALETTE["primary_hover"]),
            ],
            foreground=[("disabled", PALETTE["sidebar_muted"])],
        )

        self.style.configure(
            "Source.TEntry",
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            lightcolor=PALETTE["line"],
            darkcolor=PALETTE["line"],
            insertcolor=PALETTE["ink"],
            padding=(10, 8),
            font=self.type.body,
        )
        self.style.map(
            "Source.TEntry",
            bordercolor=[("focus", PALETTE["primary"])],
            lightcolor=[("focus", PALETTE["primary"])],
            darkcolor=[("focus", PALETTE["primary"])],
        )

        self.style.configure(
            "Processing.Horizontal.TProgressbar",
            troughcolor=PALETTE["primary_soft"],
            background=PALETTE["primary"],
            lightcolor=PALETTE["primary"],
            darkcolor=PALETTE["primary"],
            bordercolor=PALETTE["line"],
            thickness=7,
        )
        self.style.configure(
            "Error.Horizontal.TProgressbar",
            troughcolor="#FADBD8",
            background=PALETTE["danger"],
            lightcolor=PALETTE["danger"],
            darkcolor=PALETTE["danger"],
            bordercolor="#FADBD8",
            thickness=7,
        )

        self.style.configure(
            "TRadiobutton",
            background=PALETTE["surface_soft"],
            foreground=PALETTE["ink"],
            font=self.type.body,
            focuscolor=PALETTE["focus"],
        )
        self.style.map(
            "TRadiobutton",
            background=[("active", PALETTE["surface_soft"])],
        )

        self.style.configure(
            "TCheckbutton",
            background=PALETTE["canvas"],
            foreground=PALETTE["ink"],
            font=self.type.body,
            focuscolor=PALETTE["focus"],
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", PALETTE["canvas"])],
        )

        self.style.configure(
            "Settings.TCombobox",
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["ink"],
            padding=(8, 6),
            font=self.type.body,
        )

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(
            self,
            background=PALETTE["sidebar"],
            highlightbackground=PALETTE["sidebar_line"],
            highlightthickness=1,
            width=216,
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        brand = tk.Frame(sidebar, background=PALETTE["sidebar"])
        brand.pack(fill="x", padx=20, pady=(24, 20))
        if self._sidebar_icon is not None:
            tk.Label(
                brand,
                image=self._sidebar_icon,
                background=PALETTE["sidebar"],
            ).pack(side="left")
        else:
            tk.Label(
                brand,
                text="К",
                font=(self.type.family, 11, "bold"),
                foreground="#FFFFFF",
                background=PALETTE["primary"],
                width=2,
                pady=2,
            ).pack(side="left")
        tk.Label(
            brand,
            text="КОНСПЕКТ",
            font=(self.type.family, 12, "bold"),
            foreground=PALETTE["sidebar_ink"],
            background=PALETTE["sidebar"],
        ).pack(side="left", padx=(10, 0))

        new_lecture_btn = ttk.Button(
            sidebar,
            text="+ Новая лекция",
            style="SidebarPrimary.TButton",
            command=self.show_new_lecture,
        )
        new_lecture_btn.pack(fill="x", padx=16, pady=(0, 16))

        lectures_button = ttk.Button(
            sidebar,
            text="Лекции",
            style="Nav.TButton",
            command=self.show_library,
        )
        lectures_button.pack(fill="x", padx=16, pady=(0, 4))
        settings_button = ttk.Button(
            sidebar,
            text="Настройки",
            style="Nav.TButton",
            command=self.show_settings,
        )
        settings_button.pack(fill="x", padx=16, pady=(0, 4))
        self._navigation_buttons.extend((new_lecture_btn, lectures_button, settings_button))

        footer = tk.Frame(sidebar, background=PALETTE["sidebar"])
        footer.pack(side="bottom", fill="x", padx=20, pady=20)
        tk.Label(
            footer,
            text="Данные хранятся\nна этом компьютере",
            justify="left",
            font=self.type.small,
            foreground=PALETTE["sidebar_muted"],
            background=PALETTE["sidebar"],
        ).pack(anchor="w")
        ttk.Button(
            footer,
            text="Проверка системы",
            style="Nav.TButton",
            command=self.show_system_check_dialog,
        ).pack(fill="x", pady=(10, 0))

        workspace = tk.Frame(self, background=PALETTE["canvas"])
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(1, weight=1)
        self.workspace = workspace

        header = tk.Frame(workspace, background=PALETTE["canvas"], height=64)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(
            header,
            text="Учебные материалы",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).pack(side="left", padx=36, pady=20)
        tk.Label(
            header,
            text="Обработка записи — локально",
            font=self.type.small,
            foreground=PALETTE["primary"],
            background=PALETTE["primary_soft"],
            padx=10,
            pady=4,
        ).pack(side="right", padx=36, pady=18)

        content = tk.Frame(workspace, background=PALETTE["canvas"])
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self.content = content

    def _set_navigation_enabled(self, enabled: bool) -> None:
        for button in self._navigation_buttons:
            if button.winfo_exists():
                button.state(["!disabled"] if enabled else ["disabled"])

    @staticmethod
    def _load_settings_safely() -> tuple[AppSettings, str]:
        try:
            return load_settings(), ""
        except SettingsError as exc:
            diagnostic_path = record_exception("settings.load", exc)
            suffix = f" Диагностика: {diagnostic_path}" if diagnostic_path else ""
            return (
                AppSettings(),
                "Настройки не удалось загрузить. Используются безопасные значения "
                f"по умолчанию.{suffix}",
            )

    @staticmethod
    def _bind_mousewheel_tree(root: tk.Misc, canvas: tk.Canvas) -> None:
        """Scroll a canvas even while the pointer is over one of its child controls."""

        def scroll(event: tk.Event) -> str:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            return "break"

        pending: list[tk.Misc] = [root]
        while pending:
            widget = pending.pop()
            widget.bind("<MouseWheel>", scroll)
            pending.extend(widget.winfo_children())

    def show_settings(self) -> None:
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)

        header_row = tk.Frame(screen, background=PALETTE["canvas"])
        header_row.grid(row=0, column=0, sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        tk.Label(
            header_row,
            text="Настройки",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            header_row,
            text="Проверить систему",
            style="Secondary.TButton",
            command=self.show_system_check_dialog,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            screen,
            text=(
                "Выбери способ распознавания лекций и подключи личный ChatGPT или "
                "текстовый API для автоматического создания конспекта."
            ),
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 24))

        viewport = tk.Frame(screen, background=PALETTE["canvas"])
        viewport.grid(row=2, column=0, sticky="nsew")
        viewport.grid_columnconfigure(0, weight=1)
        viewport.grid_rowconfigure(0, weight=1)
        settings_canvas = tk.Canvas(
            viewport,
            background=PALETTE["canvas"],
            highlightthickness=0,
            takefocus=True,
        )
        settings_canvas.grid(row=0, column=0, sticky="nsew")
        settings_scrollbar = ttk.Scrollbar(
            viewport,
            orient="vertical",
            command=settings_canvas.yview,
        )
        settings_scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_body = tk.Frame(settings_canvas, background=PALETTE["canvas"])
        settings_body.grid_columnconfigure(0, weight=1)
        settings_window = settings_canvas.create_window(
            (0, 0),
            window=settings_body,
            anchor="nw",
        )
        settings_body.bind(
            "<Configure>",
            lambda _: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(
                settings_window,
                width=event.width,
            ),
        )
        settings_canvas.bind("<Button-1>", lambda _: settings_canvas.focus_set())
        settings_canvas.bind("<Up>", lambda _: settings_canvas.yview_scroll(-1, "units"))
        settings_canvas.bind("<Down>", lambda _: settings_canvas.yview_scroll(1, "units"))
        settings_canvas.bind(
            "<Prior>",
            lambda _: settings_canvas.yview_scroll(-1, "pages"),
        )
        settings_canvas.bind(
            "<Next>",
            lambda _: settings_canvas.yview_scroll(1, "pages"),
        )

        form = tk.Frame(settings_body, background=PALETTE["canvas"])
        form.grid(row=0, column=0, sticky="ew")
        form.grid_columnconfigure(1, weight=1)

        tk.Label(
            form,
            text="Создание конспекта через API",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            form,
            text=(
                "Необязательно. В API отправляются только транскрипция, текст слайдов "
                "и OCR. Аудио, видео, ссылка BBB и идентификатор встречи не отправляются."
            ),
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 16))

        tk.Label(
            form,
            text="Провайдер",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", padx=(0, 24), pady=8)
        providers = tk.Frame(form, background=PALETTE["surface_soft"])
        providers.grid(row=2, column=1, sticky="w", pady=8)
        ttk.Radiobutton(
            providers,
            text="OpenAI",
            value="openai",
            variable=self._settings_provider,
            command=self._settings_provider_changed,
        ).pack(side="left", padx=8, pady=4)
        ttk.Radiobutton(
            providers,
            text="DeepSeek",
            value="deepseek",
            variable=self._settings_provider,
            command=self._settings_provider_changed,
        ).pack(side="left", padx=(16, 8), pady=4)

        tk.Label(
            form,
            text="API-ключ",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=3, column=0, sticky="w", padx=(0, 24), pady=8)
        api_key_entry = ttk.Entry(
            form,
            textvariable=self._settings_api_key,
            show="•",
            style="Source.TEntry",
        )
        api_key_entry.grid(row=3, column=1, sticky="ew", pady=8)

        tk.Label(
            form,
            text="Модель API",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=4, column=0, sticky="w", padx=(0, 24), pady=8)
        ttk.Entry(
            form,
            textvariable=self._settings_api_model,
            style="Source.TEntry",
        ).grid(row=4, column=1, sticky="ew", pady=8)

        ttk.Separator(form, orient="horizontal").grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=20,
        )
        tk.Label(
            form,
            text="Личный ChatGPT",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=6, column=0, columnspan=2, sticky="w")
        tk.Label(
            form,
            text=(
                "API-ключ не нужен. Используется вход в ChatGPT и лимит Codex "
                "в твоём тарифе. После входа приложение само создаёт и сохраняет lesson.md."
            ),
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=760,
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 16))

        tk.Label(
            form,
            text="Статус",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=8, column=0, sticky="w", padx=(0, 24), pady=8)
        self._chatgpt_status_label = tk.Label(
            form,
            textvariable=self._chatgpt_status,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=620,
            justify="left",
        )
        self._chatgpt_status_label.grid(row=8, column=1, sticky="w", pady=8)

        tk.Label(
            form,
            text="Модель",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=9, column=0, sticky="w", padx=(0, 24), pady=8)
        self._chatgpt_model_combobox = ttk.Combobox(
            form,
            textvariable=self._settings_chatgpt_model,
            values=(self.settings.chatgpt_model,),
            state="readonly",
            width=28,
            style="Settings.TCombobox",
        )
        self._chatgpt_model_combobox.grid(row=9, column=1, sticky="w", pady=8)
        self._chatgpt_model_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _: self._set_active_chatgpt_model(self._settings_chatgpt_model.get()),
        )

        self._chatgpt_login_button = ttk.Button(
            form,
            text="Войти через ChatGPT",
            style="Secondary.TButton",
            command=self._start_chatgpt_login,
        )
        self._chatgpt_login_button.grid(row=10, column=1, sticky="w", pady=(8, 0))

        ttk.Separator(form, orient="horizontal").grid(
            row=11,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=20,
        )
        tk.Label(
            form,
            text="Локальная обработка записи",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=12, column=0, columnspan=2, sticky="w")
        tk.Label(
            form,
            text="Эти параметры влияют на скорость и детализацию следующей подготовки лекции.",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=13, column=0, columnspan=2, sticky="w", pady=(6, 16))

        tk.Label(
            form,
            text="Whisper",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=14, column=0, sticky="w", padx=(0, 24), pady=8)
        ttk.Combobox(
            form,
            textvariable=self._settings_whisper_model,
            values=("tiny", "base", "small"),
            state="readonly",
            width=16,
            style="Settings.TCombobox",
        ).grid(row=14, column=1, sticky="w", pady=8)

        tk.Label(
            form,
            text="Кадры экрана",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=15, column=0, sticky="w", padx=(0, 24), pady=8)
        interval = tk.Frame(form, background=PALETTE["canvas"])
        interval.grid(row=15, column=1, sticky="w", pady=8)
        ttk.Combobox(
            interval,
            textvariable=self._settings_frame_interval,
            values=("30", "60", "90"),
            state="readonly",
            width=8,
            style="Settings.TCombobox",
        ).pack(side="left")
        tk.Label(
            interval,
            text="секунд",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).pack(side="left", padx=(10, 0))

        ttk.Checkbutton(
            form,
            text="Обрабатывать демонстрацию экрана (кадры и OCR)",
            variable=self._settings_ocr_enabled,
        ).grid(row=16, column=1, sticky="w", pady=(10, 0))

        actions = tk.Frame(settings_body, background=PALETTE["canvas"])
        actions.grid(row=1, column=0, sticky="ew", pady=(24, 12))
        ttk.Button(
            actions,
            text="Сохранить настройки",
            style="Primary.TButton",
            command=self._save_settings_from_form,
        ).pack(side="left")
        self._settings_status_label = tk.Label(
            actions,
            textvariable=self._settings_status,
            font=self.type.small,
            foreground=(PALETTE["danger"] if self._settings_load_warning else PALETTE["muted"]),
            background=PALETTE["canvas"],
            wraplength=560,
            justify="left",
        )
        self._settings_status_label.pack(side="left", padx=(16, 0))

        self._bind_mousewheel_tree(settings_canvas, settings_canvas)

        self._show_screen(screen, animated=True)
        self._refresh_chatgpt_account()

    def _settings_provider_changed(self) -> None:
        provider = self._settings_provider.get().strip().lower()
        self._settings_api_model.set(default_model_for_provider(provider))
        self._set_settings_status("", PALETTE["muted"])

    def _save_settings_from_form(self) -> None:
        try:
            frame_interval = int(self._settings_frame_interval.get())
        except ValueError:
            self._set_settings_status(
                "Выбери интервал кадров: 30, 60 или 90 секунд.",
                PALETTE["danger"],
            )
            return

        proposed = AppSettings(
            api_provider=self._settings_provider.get().strip().lower(),
            api_model=self._settings_api_model.get().strip(),
            api_key=self._settings_api_key.get().strip(),
            chatgpt_model=self._settings_chatgpt_model.get().strip(),
            whisper_model=self._settings_whisper_model.get().strip().lower(),
            frame_interval_seconds=frame_interval,
            ocr_enabled=self._settings_ocr_enabled.get(),
        )
        try:
            save_settings(proposed)
        except SettingsError as exc:
            diagnostic_path = record_exception("settings.save", exc)
            suffix = f" Диагностика: {diagnostic_path}" if diagnostic_path else ""
            self._set_settings_status(f"{exc}{suffix}", PALETTE["danger"])
            return

        self.settings = proposed
        self._set_active_chatgpt_model(proposed.chatgpt_model)
        self._settings_load_warning = ""
        if proposed.api_configured:
            message = (
                f"Сохранено. API {proposed.provider_label} подключён; "
                "ключ хранится в системном защищённом хранилище."
            )
        else:
            message = (
                "Сохранено. API не подключён; личный ChatGPT и веб-чат DeepSeek остаются доступны."
            )
        self._set_settings_status(message, PALETTE["success"])

    def _set_settings_status(self, message: str, color: str) -> None:
        self._settings_status.set(message)
        label = self._settings_status_label
        if label is not None and label.winfo_exists():
            label.configure(foreground=color)

    def _next_chatgpt_account_operation(self) -> int:
        self._chatgpt_account_operation_id += 1
        return self._chatgpt_account_operation_id

    def _refresh_chatgpt_account(self) -> None:
        if self._chatgpt_login_active:
            return
        operation_id = self._next_chatgpt_account_operation()
        self._set_chatgpt_status("Проверяем состояние входа…", PALETTE["muted"])
        self._set_chatgpt_controls_busy(True)
        if hasattr(self, "_job_runner"):
            self._start_chatgpt_account_job(False, operation_id)
            return
        threading.Thread(
            target=self._chatgpt_account_worker,
            args=(False, operation_id),
            daemon=True,
        ).start()

    def _start_chatgpt_login(self) -> None:
        if self._chatgpt_login_active:
            return
        self._chatgpt_login_active = True
        operation_id = self._next_chatgpt_account_operation()
        self._set_chatgpt_status(
            "Заверши вход в открывшемся окне. После этого список моделей обновится автоматически.",
            PALETTE["muted"],
        )
        self._set_chatgpt_controls_busy(True)
        if hasattr(self, "_job_runner"):
            self._start_chatgpt_account_job(True, operation_id)
            return
        threading.Thread(
            target=self._chatgpt_account_worker,
            args=(True, operation_id),
            daemon=True,
        ).start()

    def _start_chatgpt_account_job(self, should_login: bool, operation_id: int) -> None:
        def task(
            token: CancellationToken, _: object
        ) -> tuple[ChatGPTAccountStatus, list[ChatGPTModel], str]:
            token.check_cancelled()
            status = login_with_chatgpt() if should_login else chatgpt_account_status()
            models: list[ChatGPTModel] = []
            model_error = ""
            if status.signed_in:
                try:
                    models = list_chatgpt_models()
                except ChatGPTAccountError as exc:
                    model_error = str(exc)
            return status, models, model_error

        def event_handler(event: JobEvent) -> None:
            def apply_event() -> None:
                if operation_id != self._chatgpt_account_operation_id:
                    return
                if event.event_type is JobEventType.COMPLETED and isinstance(event.result, tuple):
                    status, models, model_error = event.result
                    self._finish_chatgpt_account_refresh(operation_id, status, models, model_error)
                elif event.event_type is JobEventType.CANCELLED:
                    self._finish_chatgpt_account_error(operation_id, "Операция отменена.")
                elif event.event_type is JobEventType.FAILED:
                    self._finish_chatgpt_account_error(
                        operation_id, event.error or "Не удалось проверить вход."
                    )

            self.after(0, apply_event)

        self._job_runner.run_job(task, event_handler)

    def _chatgpt_account_worker(
        self,
        should_login: bool,
        operation_id: int,
    ) -> None:
        try:
            status = login_with_chatgpt() if should_login else chatgpt_account_status()
        except ChatGPTAccountError as exc:
            message = str(exc)
            self.after(
                0,
                lambda message=message: self._finish_chatgpt_account_error(
                    operation_id,
                    message,
                ),
            )
            return

        models: list[ChatGPTModel] = []
        model_error = ""
        if status.signed_in:
            try:
                models = list_chatgpt_models()
            except ChatGPTAccountError as exc:
                model_error = str(exc)

        self.after(
            0,
            lambda status=status, models=models, model_error=model_error: (
                self._finish_chatgpt_account_refresh(
                    operation_id,
                    status,
                    models,
                    model_error,
                )
            ),
        )

    def _finish_chatgpt_account_refresh(
        self,
        operation_id: int,
        status: ChatGPTAccountStatus,
        models: list[ChatGPTModel],
        model_error: str = "",
    ) -> None:
        if operation_id != self._chatgpt_account_operation_id:
            return
        self._chatgpt_login_active = False
        self._chatgpt_account = status
        self._set_chatgpt_controls_busy(False)

        if status.signed_in:
            details = ["Вход выполнен"]
            if status.email:
                details.append(status.email)
            if status.plan_type:
                details.append(f"тариф {status.plan_type}")
            message = " · ".join(details)
            color = PALETTE["success"]
            self._chatgpt_generation_action.set("Создать через ChatGPT")
        else:
            message = "Вход не выполнен"
            color = PALETTE["muted"]
            self._chatgpt_generation_action.set("Войти и создать")

        if models:
            slugs = tuple(dict.fromkeys(model.slug for model in models if model.slug))
            combobox = self._chatgpt_model_combobox
            if combobox is not None and combobox.winfo_exists():
                combobox.configure(values=slugs)
            current = self._settings_chatgpt_model.get().strip()
            if slugs:
                selected_model = current if current in slugs else slugs[0]
                self._set_active_chatgpt_model(selected_model)
        if model_error:
            message = f"{message}. Не удалось обновить модели: {model_error}"
            color = PALETTE["danger"]
        self._set_chatgpt_status(message, color)

    def _finish_chatgpt_account_error(
        self,
        operation_id: int,
        message: str,
    ) -> None:
        if operation_id != self._chatgpt_account_operation_id:
            return
        self._chatgpt_login_active = False
        self._set_chatgpt_controls_busy(False)
        if self._chatgpt_account is None or not self._chatgpt_account.signed_in:
            self._chatgpt_generation_action.set("Войти и создать")
        self._set_chatgpt_status(
            f"Не удалось проверить вход: {message}",
            PALETTE["danger"],
        )

    def _set_chatgpt_controls_busy(self, busy: bool) -> None:
        button = self._chatgpt_login_button
        if button is not None and button.winfo_exists():
            button.state(["disabled"] if busy else ["!disabled"])
        combobox = self._chatgpt_model_combobox
        if combobox is not None and combobox.winfo_exists():
            combobox.configure(state="disabled" if busy else "readonly")

    def _set_chatgpt_status(self, message: str, color: str) -> None:
        self._chatgpt_status.set(message)
        label = self._chatgpt_status_label
        if label is not None and label.winfo_exists():
            label.configure(foreground=color)

    def _set_active_chatgpt_model(self, model: str) -> None:
        selected_model = model.strip()
        if not selected_model:
            return
        self._settings_chatgpt_model.set(selected_model)
        if self.settings.chatgpt_model != selected_model:
            self.settings = replace(self.settings, chatgpt_model=selected_model)
        self._chatgpt_model_summary.set(
            "API-ключ не нужен; используется лимит Codex твоего тарифа. "
            "Конспект сохранится автоматически."
        )

    def show_library(self, animated: bool = True) -> None:
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)

        intro = tk.Frame(screen, background=PALETTE["canvas"])
        intro.grid(row=0, column=0, sticky="ew")
        intro.grid_columnconfigure(0, weight=1)
        tk.Label(
            intro,
            text="Моя библиотека",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            intro,
            text="Все записи, конспекты и материалы по лекциям собраны здесь.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(
            intro,
            text="Новая лекция  →",
            style="Primary.TButton",
            command=self.show_new_lecture,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        divider = tk.Frame(screen, background=PALETTE["line"], height=1)
        divider.grid(row=1, column=0, sticky="ew", pady=(24, 0))

        if self.library:
            self._build_library_list(screen)
        else:
            self._build_empty_library(screen)

        self._show_screen(screen, animated)

    def _build_empty_library(self, screen: ttk.Frame) -> None:
        empty = tk.Frame(screen, background=PALETTE["canvas"])
        empty.grid(row=2, column=0, sticky="nsew")
        empty.grid_columnconfigure(0, weight=1)
        empty.grid_rowconfigure(0, weight=1)

        message = tk.Frame(empty, background=PALETTE["canvas"])
        message.grid(row=0, column=0)
        tk.Label(
            message,
            text="Библиотека пока пуста",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).pack()
        tk.Label(
            message,
            text="Добавь первую запись — здесь появится её конспект\nи все материалы для повторения.",
            justify="center",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).pack(pady=(8, 20))
        ttk.Button(
            message,
            text="Добавить первую лекцию",
            style="Secondary.TButton",
            command=self.show_new_lecture,
        ).pack()

    def _build_library_list(self, screen: ttk.Frame) -> None:
        listing = tk.Frame(screen, background=PALETTE["canvas"])
        listing.grid(row=2, column=0, sticky="nsew", pady=(18, 0))
        listing.grid_columnconfigure(0, weight=1)
        listing.grid_rowconfigure(2, weight=1)

        toolbar = tk.Frame(listing, background=PALETTE["canvas"])
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        toolbar.grid_columnconfigure(0, weight=1)
        search = ttk.Entry(
            toolbar, textvariable=self._library_query, width=24, style="Source.TEntry"
        )
        search.grid(row=0, column=0, sticky="ew")
        search.configure(takefocus=True)
        search.bind("<Return>", lambda _: self.show_library(animated=False))
        ttk.Button(
            toolbar,
            text="Найти",
            style="Secondary.TButton",
            command=lambda: self.show_library(animated=False),
        ).grid(row=0, column=1, padx=(8, 0))
        state_values = (
            "Все состояния",
            "Импортировано",
            "Подготовлено",
            "Пакет готов",
            "Конспект готов",
        )
        state_combo = ttk.Combobox(
            toolbar,
            textvariable=self._library_state_filter,
            values=state_values,
            state="readonly",
            width=16,
            style="Settings.TCombobox",
        )
        state_combo.grid(row=0, column=2, padx=(8, 0))
        state_combo.bind("<<ComboboxSelected>>", lambda _: self.show_library(animated=False))

        date_values = ("Все даты", "Сегодня", "За 7 дней", "За 30 дней")
        date_combo = ttk.Combobox(
            toolbar,
            textvariable=self._library_date_filter,
            values=date_values,
            state="readonly",
            width=12,
            style="Settings.TCombobox",
        )
        date_combo.grid(row=0, column=3, padx=(8, 0))
        date_combo.bind("<<ComboboxSelected>>", lambda _: self.show_library(animated=False))

        sort_combo = ttk.Combobox(
            toolbar,
            textvariable=self._library_sort,
            values=("Сначала новые", "Сначала старые", "По названию"),
            state="readonly",
            width=15,
            style="Settings.TCombobox",
        )
        sort_combo.grid(row=0, column=4, padx=(8, 0))
        sort_combo.bind("<<ComboboxSelected>>", lambda _: self.show_library(animated=False))

        ttk.Button(
            toolbar,
            text="Корзина",
            style="Secondary.TButton",
            command=self.show_trash_dialog,
        ).grid(row=0, column=5, padx=(8, 0))

        tk.Label(
            toolbar,
            textvariable=self._library_storage_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=6, padx=(12, 0))

        def _calc_size_worker():
            try:
                sz = calculate_library_size(self.library, self.app_paths.data_dir)
                self.after(
                    0, lambda: self._library_storage_status.set(f"Занято: {self._format_bytes(sz)}")
                )
            except Exception:
                pass

        threading.Thread(target=_calc_size_worker, daemon=True).start()

        state_map = {
            "Импортировано": "imported",
            "Подготовлено": "prepared",
            "Пакет готов": "package_ready",
            "Конспект готов": "lesson_ready",
        }
        state_filter = state_map.get(self._library_state_filter.get())
        date_map = {"Сегодня": "today", "За 7 дней": "7_days", "За 30 дней": "30_days"}
        date_filter = date_map.get(self._library_date_filter.get())
        from .workflow import LectureState

        filtered = filter_and_sort_recordings(
            self.library,
            query=self._library_query.get(),
            state_filter=LectureState(state_filter) if state_filter else None,
            sort_by=(
                "title_asc"
                if self._library_sort.get() == "По названию"
                else "date_asc"
                if self._library_sort.get() == "Сначала старые"
                else "date_desc"
            ),
            date_filter=date_filter,
            base_dir=self.app_paths.data_dir,
        )
        tk.Label(
            listing,
            text=f"Показано: {len(filtered)} из {len(self.library)}",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        canvas = tk.Canvas(
            listing,
            background=PALETTE["canvas"],
            highlightthickness=0,
            takefocus=True,
        )
        canvas.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=2, column=1, sticky="ns", padx=(10, 0))
        canvas.configure(yscrollcommand=scrollbar.set)

        rows = tk.Frame(canvas, background=PALETTE["canvas"])
        rows.grid_columnconfigure(0, weight=1)
        rows_window = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind(
            "<Configure>",
            lambda _: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(rows_window, width=event.width),
        )
        canvas.bind("<Button-1>", lambda _: canvas.focus_set())
        canvas.bind("<Up>", lambda _: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Down>", lambda _: canvas.yview_scroll(1, "units"))
        canvas.bind("<Prior>", lambda _: canvas.yview_scroll(-1, "pages"))
        canvas.bind("<Next>", lambda _: canvas.yview_scroll(1, "pages"))
        canvas.bind("<Home>", lambda _: canvas.yview_moveto(0.0))
        canvas.bind("<End>", lambda _: canvas.yview_moveto(1.0))

        for index, recording in enumerate(filtered):
            row = tk.Frame(
                rows,
                background=PALETTE["surface"],
                highlightbackground=PALETTE["line"],
                highlightthickness=1,
                padx=18,
                pady=14,
            )
            row.grid(row=index, column=0, sticky="ew", pady=(0, 6))
            row.grid_columnconfigure(0, weight=1)
            tk.Label(
                row,
                text=recording.title,
                font=self.type.body_bold,
                foreground=PALETTE["ink"],
                background=PALETTE["surface"],
                wraplength=560,
                justify="left",
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                row,
                text=self._recording_summary(recording),
                font=self.type.small,
                foreground=PALETTE["muted"],
                background=PALETTE["surface"],
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))

            prepared = lecture_is_prepared(recording)
            package_ready = prepared and context_package_is_ready(recording)
            lesson_ready = lesson_is_ready(recording)

            if lesson_ready:
                status_text = "Конспект готов"
                status_color = PALETTE["success"]
                action_text = "Открыть конспект"
                action_style = "Primary.TButton"

                def on_action_reader(item=recording) -> None:
                    self.show_lesson_reader(item)

                action = on_action_reader
                action_state = "normal"
            elif not prepared:
                status_text = "Материалы не подготовлены"
                status_color = PALETTE["muted"]
                action_text = "Подготовить"
                action_style = "Primary.TButton"

                def on_action_prep(item=recording) -> None:
                    self.start_local_processing(item)

                action = on_action_prep
                action_state = "normal"
            elif not package_ready:
                status_text = "Транскрипция готова"
                status_color = PALETTE["success"]
                action_text = "Собрать пакет"
                action_style = "Primary.TButton"

                def on_action_pkg(item=recording) -> None:
                    self.start_context_packaging(item)

                action = on_action_pkg
                action_state = "normal"
            else:
                status_text = "Готово к созданию"
                status_color = PALETTE["success"]
                action_text = "Создать конспект"
                action_style = "Primary.TButton"

                def on_action_choice(item=recording) -> None:
                    self.show_chat_provider_choice(item)

                action = on_action_choice
                action_state = "normal"

            right_meta = tk.Frame(row, background=PALETTE["surface"])
            right_meta.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
            tk.Label(
                right_meta,
                text=format_imported_at(recording.imported_at),
                font=self.type.small,
                foreground=PALETTE["faint"],
                background=PALETTE["surface"],
            ).pack(anchor="e")
            tk.Label(
                right_meta,
                text=status_text,
                font=self.type.small,
                foreground=status_color,
                background=PALETTE["surface"],
            ).pack(anchor="e", pady=(2, 0))

            ttk.Button(
                row,
                text=action_text,
                style=action_style,
                command=action,
                state=action_state,
            ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(16, 0))

            management = tk.Frame(row, background=PALETTE["surface"])
            management.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
            ttk.Button(
                management,
                text="Переименовать",
                style="Secondary.TButton",
                command=lambda item=recording: self._rename_library_recording(item),
            ).pack(side="left")
            ttk.Button(
                management,
                text="В корзину",
                style="Secondary.TButton",
                command=lambda item=recording: self._trash_library_recording(item),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                management,
                text="Открыть папку",
                style="Secondary.TButton",
                command=lambda item=recording: self._open_lecture_folder(item),
            ).pack(side="left", padx=(6, 0))

        self._bind_mousewheel_tree(canvas, canvas)

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(max(0, value))
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if size < 1024 or unit == "ТБ":
                return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
            size /= 1024
        return "0 Б"

    def _open_lecture_folder(self, recording: BBBRecording) -> None:
        from .platform_services import PlatformSystemActions

        target = default_lecture_directory(recording, base_dir=self.app_paths.data_dir)
        target.mkdir(parents=True, exist_ok=True)
        PlatformSystemActions().open_in_file_manager(target)

    def show_trash_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Корзина лекций")
        dialog.geometry("640x480")
        dialog.minsize(500, 360)
        dialog.configure(background=PALETTE["canvas"])
        dialog.transient(self)
        dialog.grab_set()

        header = tk.Frame(dialog, background=PALETTE["canvas"], padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Корзина удалённых лекций",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).pack(anchor="w")

        content_frame = tk.Frame(dialog, background=PALETTE["canvas"], padx=24)
        content_frame.pack(fill="both", expand=True)

        footer = tk.Frame(dialog, background=PALETTE["canvas"], padx=24, pady=16)
        footer.pack(fill="x", side="bottom")

        def refresh_trash() -> None:
            for child in content_frame.winfo_children():
                child.destroy()
            items = list_trash(self.app_paths.data_dir)
            if not items:
                tk.Label(
                    content_frame,
                    text="Корзина пуста.",
                    font=self.type.body,
                    foreground=PALETTE["muted"],
                    background=PALETTE["canvas"],
                ).pack(pady=40)
                empty_btn.configure(state="disabled")
                return

            empty_btn.configure(state="normal")
            for item in items:
                row = tk.Frame(
                    content_frame,
                    background=PALETTE["surface"],
                    highlightbackground=PALETTE["line"],
                    highlightthickness=1,
                    padx=14,
                    pady=10,
                )
                row.pack(fill="x", pady=4)
                row.grid_columnconfigure(0, weight=1)
                tk.Label(
                    row,
                    text=item.get("original_title", "Без названия"),
                    font=self.type.body_bold,
                    foreground=PALETTE["ink"],
                    background=PALETTE["surface"],
                    anchor="w",
                ).grid(row=0, column=0, sticky="w")
                size_str = self._format_bytes(item.get("total_bytes", 0))
                date_str = str(item.get("trashed_at", ""))[:10]
                tk.Label(
                    row,
                    text=f"Удалено: {date_str} · {size_str}",
                    font=self.type.small,
                    foreground=PALETTE["muted"],
                    background=PALETTE["surface"],
                ).grid(row=1, column=0, sticky="w")

                def do_restore(m_id=item.get("meeting_id", ""), src=item.get("source_url")) -> None:
                    try:
                        restore_from_trash(
                            self.app_paths.library_path,
                            m_id,
                            self.app_paths.data_dir,
                            source_url=src,
                        )
                        self.library = self._load_library_safely()
                        self.show_library(animated=False)
                        refresh_trash()
                    except Exception as exc:
                        from tkinter import messagebox

                        messagebox.showerror("Ошибка восстановления", str(exc))

                ttk.Button(
                    row,
                    text="Восстановить",
                    style="Secondary.TButton",
                    command=do_restore,
                ).grid(row=0, column=1, rowspan=2, padx=(8, 0))

        def do_empty() -> None:
            from tkinter import messagebox

            if messagebox.askyesno(
                "Очистить корзину", "Безвозвратно удалить все материалы из корзины?"
            ):
                empty_trash(self.app_paths.data_dir)
                refresh_trash()

        empty_btn = ttk.Button(
            footer,
            text="Очистить корзину",
            style="Secondary.TButton",
            command=do_empty,
        )
        empty_btn.pack(side="left")

        ttk.Button(
            footer,
            text="Закрыть",
            style="Primary.TButton",
            command=dialog.destroy,
        ).pack(side="right")

        refresh_trash()

    def show_system_check_dialog(self) -> None:
        from .diagnostics import collect_system_diagnostics

        diag = collect_system_diagnostics(self.app_paths)
        dialog = tk.Toplevel(self)
        dialog.title("Проверка системы")
        dialog.geometry("540x440")
        dialog.minsize(460, 360)
        dialog.configure(background=PALETTE["canvas"])
        dialog.transient(self)
        dialog.grab_set()

        body = tk.Frame(dialog, background=PALETTE["canvas"], padx=24, pady=20)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="Готовность системы",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).pack(anchor="w", pady=(0, 16))

        deps = diag.get("dependencies", {})
        items = [
            ("FFmpeg (извлечение аудио и кадров)", deps.get("ffmpeg_available", False)),
            ("Tesseract OCR (распознавание текста)", deps.get("tesseract_available", False)),
            ("Codex CLI (личный ChatGPT)", deps.get("codex_available", False)),
            ("Папки приложения доступны для записи", diag.get("status") == "ok"),
        ]

        for label, ok in items:
            row = tk.Frame(
                body,
                background=PALETTE["surface"],
                highlightbackground=PALETTE["line"],
                highlightthickness=1,
                padx=14,
                pady=10,
            )
            row.pack(fill="x", pady=4)
            icon_text = "✓" if ok else "✕"
            icon_color = PALETTE["success"] if ok else PALETTE["danger"]
            tk.Label(
                row,
                text=icon_text,
                font=self.type.body_bold,
                foreground=icon_color,
                background=PALETTE["surface"],
                width=3,
            ).pack(side="left")
            tk.Label(
                row,
                text=label,
                font=self.type.body,
                foreground=PALETTE["ink"],
                background=PALETTE["surface"],
            ).pack(side="left", padx=8)

        ttk.Button(
            body,
            text="Закрыть",
            style="Primary.TButton",
            command=dialog.destroy,
        ).pack(side="bottom", pady=(20, 0), anchor="e")

    def _rename_library_recording(self, recording: BBBRecording) -> None:
        from tkinter import simpledialog

        title = simpledialog.askstring(
            "Переименовать лекцию", "Новое название:", initialvalue=recording.title
        )
        if title is None:
            return
        try:
            rename_recording(
                self.app_paths.library_path,
                recording.meeting_id,
                title,
                source_url=recording.source_url,
            )
            self.library = self._load_library_safely()
            self.show_library(animated=False)
        except (ValueError, BBBImportError) as exc:
            self._set_import_status(str(exc), PALETTE["danger"])

    def _trash_library_recording(self, recording: BBBRecording) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno(
            "Переместить в корзину", f"Переместить «{recording.title}» в корзину?"
        ):
            return
        try:
            move_to_trash(
                self.app_paths.library_path,
                recording.meeting_id,
                self.app_paths.data_dir,
                source_url=recording.source_url,
            )
            self.library = self._load_library_safely()
            self.show_library(animated=False)
        except (ValueError, BBBImportError, OSError) as exc:
            self._set_import_status(str(exc), PALETTE["danger"])

    def show_new_lecture(self) -> None:
        self._import_status.set("")
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)

        ttk.Button(
            screen,
            text="← К библиотеке",
            style="Secondary.TButton",
            command=self.show_library,
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            screen,
            text="Новая лекция",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))
        tk.Label(
            screen,
            text="Выбери способ добавления учебного материала.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", pady=(6, 20))

        bbb_panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        bbb_panel.grid(row=3, column=0, sticky="ew")
        bbb_panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            bbb_panel,
            text="Ссылка на запись BigBlueButton",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            bbb_panel,
            text="Видео не будет скачиваться сейчас: сначала сохраним потоки и тексты слайдов в библиотеку.",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))
        source_entry = ttk.Entry(
            bbb_panel,
            textvariable=self._bbb_url,
            style="Source.TEntry",
        )
        source_entry.grid(row=2, column=0, sticky="ew")
        source_entry.focus_set()
        source_entry.bind("<Return>", lambda _: self.start_bbb_import())

        self._import_button = ttk.Button(
            bbb_panel,
            text="Проверить и добавить",
            style="Primary.TButton",
            command=self.start_bbb_import,
        )
        self._import_button.grid(row=3, column=0, sticky="w", pady=(14, 0))

        file_panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=20,
            pady=18,
        )
        file_panel.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        file_panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            file_panel,
            text="Локальный файл с диска",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            file_panel,
            text="Импорт записанной лекции с компьютера (аудио или видео MP4, MP3, WAV, MKV).",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))
        ttk.Button(
            file_panel,
            text="Выбрать файл с диска",
            style="Secondary.TButton",
            command=self.start_local_media_picker,
        ).grid(row=2, column=0, sticky="w")

        self._import_status_label = tk.Label(
            screen,
            textvariable=self._import_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=700,
            justify="left",
        )
        self._import_status_label.grid(
            row=5,
            column=0,
            sticky="w",
            pady=(16, 0),
        )

        self._show_screen(screen, animated=True)

    def start_local_media_picker(self) -> None:
        from tkinter import filedialog

        from .local_media_import import LocalMediaImportError, import_local_media_file

        chosen = filedialog.askopenfilename(
            title="Выбери аудио или видео лекции",
            filetypes=[
                ("Медиафайлы", "*.mp4 *.mp3 *.m4a *.wav *.mkv *.webm *.aac"),
                ("Все файлы", "*.*"),
            ],
        )
        if not chosen:
            return
        try:
            recording, _ = import_local_media_file(Path(chosen))
            self._finish_import_success(recording)
        except LocalMediaImportError as exc:
            self._set_import_status(str(exc), PALETTE["danger"])
        except Exception as exc:
            self._set_import_status(f"Ошибка импорта: {exc}", PALETTE["danger"])

    def start_bbb_import(self) -> None:
        playback_url = self._bbb_url.get().strip()
        if not playback_url:
            self._set_import_status(
                "Вставь ссылку вида https://…/playback.html?meetingId=…",
                PALETTE["danger"],
            )
            return
        if self._import_button is not None:
            self._import_button.state(["disabled"])
        self._set_import_status("Проверяем запись BBB и доступные материалы…", PALETTE["muted"])
        if hasattr(self, "_job_runner"):

            def task(token: CancellationToken, _: object) -> BBBRecording:
                token.check_cancelled()
                recording = inspect_bbb_recording(playback_url)
                save_to_library(recording)
                return recording

            def on_event(event: JobEvent) -> None:
                if event.event_type is JobEventType.COMPLETED:
                    self.after(0, lambda: self._finish_import_success(event.result))
                elif event.event_type is JobEventType.CANCELLED:
                    self.after(0, lambda: self._finish_import_error("Импорт отменён."))
                elif event.event_type is JobEventType.FAILED:
                    self.after(
                        0,
                        lambda: self._finish_import_error(
                            event.error or "Не удалось импортировать запись."
                        ),
                    )

            self._job_runner.run_job(task, on_event)
            return
        threading.Thread(
            target=self._import_bbb_worker,
            args=(playback_url,),
            daemon=True,
        ).start()

    def _import_bbb_worker(self, playback_url: str) -> None:
        try:
            recording = inspect_bbb_recording(playback_url)
            save_to_library(recording)
        except BBBImportError as exc:
            message = str(exc)
            self.after(0, lambda message=message: self._finish_import_error(message))
        except Exception as exc:
            record_exception("bbb.import", exc)
            self.after(
                0,
                lambda: self._finish_import_error(
                    "Не удалось подключиться к записи. Проверь ссылку и попробуй ещё раз."
                ),
            )
        else:
            self.after(0, lambda: self._finish_import_success(recording))

    def _finish_import_success(self, recording: BBBRecording) -> None:
        self.library = self._load_library_safely()
        parts = ["звук"]
        if recording.has_screen_share:
            parts.append("демонстрация экрана")
        if recording.slides:
            parts.append(f"слайды: {len(recording.slides)}")
        self._set_import_status(
            f"«{recording.title}» добавлена: {', '.join(parts)}. Открой библиотеку, чтобы проверить запись.",
            PALETTE["success"],
        )
        if self._import_button is not None and self._import_button.winfo_exists():
            self._import_button.state(["!disabled"])

    def _finish_import_error(self, message: str) -> None:
        self._set_import_status(message, PALETTE["danger"])
        if self._import_button is not None and self._import_button.winfo_exists():
            self._import_button.state(["!disabled"])

    def _set_import_status(self, text: str, color: str) -> None:
        self._import_status.set(text)
        if self._import_status_label is not None and self._import_status_label.winfo_exists():
            self._import_status_label.configure(foreground=color)

    @staticmethod
    def _recording_summary(recording: BBBRecording) -> str:
        parts = ["BBB", "звук"]
        if recording.has_screen_share:
            parts.append("экран")
        if recording.slides:
            parts.append(f"слайдов: {len(recording.slides)}")
        suffix = recording.meeting_id[-8:] if recording.meeting_id else "—"
        parts.append(f"ID …{suffix}")
        return " · ".join(parts)

    @staticmethod
    def _load_library_safely() -> list[BBBRecording]:
        try:
            return load_library()
        except BBBImportError:
            return []

    def start_local_processing(self, recording: BBBRecording) -> None:
        if self._processing_active:
            return
        operation_id = self._prepare_processing_state(
            recording,
            kind="local",
            message="Подготовка начнётся после проверки локальных инструментов…",
        )
        self.show_processing_screen(recording)
        self._start_durable_processing_job(
            operation_id,
            lambda token, progress: prepare_lecture(
                recording,
                model_name=self.settings.whisper_model,
                frame_interval_seconds=self.settings.frame_interval_seconds,
                enable_ocr=self.settings.ocr_enabled,
                progress=progress,
                cancellation_token=token,
            ),
            self._finish_processing_success,
        )

    def start_context_packaging(self, recording: BBBRecording) -> None:
        if self._processing_active:
            return
        operation_id = self._prepare_processing_state(
            recording,
            kind="package",
            message="Собираем локальный текстовый пакет для создания конспекта…",
        )
        self.show_processing_screen(
            recording,
            heading="Собираем пакет контекста",
            description=(
                "Объединим транскрипцию, текст слайдов и OCR экрана. "
                "Нейросеть и платные API на этом шаге не используются."
            ),
        )
        self._start_durable_processing_job(
            operation_id,
            lambda token, progress: build_context_package(
                recording,
                progress=progress,
                cancellation_token=token,
            ),
            self._finish_context_package_success,
        )

    def _start_durable_processing_job(
        self,
        operation_id: int,
        target,
        on_success,
    ) -> None:
        """Run local preparation through JobRunner and marshal events to Tk."""
        token = CancellationToken()
        self._processing_token = token

        def on_event(event: JobEvent) -> None:
            self.after(
                0,
                lambda event=event: self._handle_processing_event(operation_id, event, on_success),
            )

        self._job_runner.run_job(target, on_event, token=token)

    def _handle_processing_event(self, operation_id: int, event: JobEvent, on_success) -> None:
        if not _operation_is_current(self, operation_id):
            return
        if event.event_type is JobEventType.PROGRESS:
            self._set_processing_progress(event.percent, event.message)
        elif event.event_type is JobEventType.COMPLETED:
            self._processing_token = None
            on_success(event.result)
        elif event.event_type is JobEventType.CANCELLED:
            self._processing_token = None
            self._finish_processing_cancelled()
        elif event.event_type is JobEventType.FAILED:
            self._processing_token = None
            error = RuntimeError(event.error or event.message or "Ошибка выполнения задачи.")
            diagnostic_path = record_exception("processing.job", error)
            self._processing_diagnostic_path = diagnostic_path
            self._finish_processing_error(
                event.error or "Подготовка остановлена из-за неожиданной ошибки."
            )

    def cancel_active_processing(self) -> None:
        token = self._processing_token
        if token is None or not self._processing_active:
            return
        token.cancel()
        self._processing_status.set("Отменяем операцию…")
        if self._processing_cancel_button is not None:
            self._processing_cancel_button.state(["disabled"])

    def _finish_processing_cancelled(self) -> None:
        self._processing_active = False
        self._set_navigation_enabled(True)
        self._processing_state.set("Отменено")
        self._processing_percent.set("Отменено")
        self._processing_status.set(
            "Операция отменена. Уже скачанные материалы сохранены; её можно возобновить."
        )
        if self._processing_cancel_button is not None:
            self._processing_cancel_button.pack_forget()
        self._enable_processing_return()

    def _prepare_processing_state(
        self,
        recording: BBBRecording,
        *,
        kind: str,
        message: str,
    ) -> int:
        now = time.monotonic()
        self._active_processing_recording = recording
        self._active_processing_kind = kind
        self._processing_started_at = now
        self._processing_last_activity_at = now
        self._processing_active = True
        self._processing_operation_id += 1
        self._processing_heartbeat_id += 1
        self._set_navigation_enabled(False)
        self._processing_diagnostic_path = None
        self._processing_diagnostic.set("")
        self._processing_state.set("Выполняется")
        self._processing_percent.set("0%")
        self._processing_status.set(message)
        self._processing_activity.set("Прошло 00:00 · последнее обновление только что")
        return self._processing_operation_id

    def show_processing_screen(
        self,
        recording: BBBRecording,
        *,
        heading: str = "Подготавливаем материалы",
        description: str = "Аудио и кадры будут обработаны на этом компьютере. Платные API не используются.",
    ) -> None:
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 36, 36, 36))
        screen.grid_columnconfigure(0, weight=1)

        tk.Label(
            screen,
            text=heading,
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, sticky="w")

        panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=24,
            pady=24,
        )
        panel.grid(row=1, column=0, sticky="ew", pady=(24, 0))
        panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text=recording.title,
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=(f"{format_imported_at(recording.imported_at)} · ID …{recording.meeting_id[-8:]}"),
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tk.Label(
            panel,
            text=description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=700,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(10, 20))

        progress_header = tk.Frame(panel, background=PALETTE["surface"])
        progress_header.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        progress_header.grid_columnconfigure(0, weight=1)
        tk.Label(
            progress_header,
            textvariable=self._processing_state,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        self._processing_percent_label = tk.Label(
            progress_header,
            textvariable=self._processing_percent,
            font=self.type.small,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        )
        self._processing_percent_label.grid(row=0, column=1, sticky="e")
        self._processing_progress = ttk.Progressbar(
            panel,
            mode="determinate",
            maximum=100,
            value=0,
            style="Processing.Horizontal.TProgressbar",
        )
        self._processing_progress.grid(row=4, column=0, sticky="ew")
        self._processing_status_label = tk.Label(
            panel,
            textvariable=self._processing_status,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            justify="left",
            wraplength=720,
        )
        self._processing_status_label.grid(row=5, column=0, sticky="w", pady=(16, 0))
        tk.Label(
            panel,
            textvariable=self._processing_activity,
            font=self.type.small,
            foreground=PALETTE["faint"],
            background=PALETTE["surface"],
        ).grid(row=6, column=0, sticky="w", pady=(6, 16))

        actions = tk.Frame(panel, background=PALETTE["surface"])
        actions.grid(row=7, column=0, sticky="w")
        self._processing_retry_button = ttk.Button(
            actions,
            text="Повторить",
            style="Primary.TButton",
            command=self._retry_active_processing,
        )
        self._processing_retry_button.pack(side="left")
        self._processing_retry_button.pack_forget()
        self._processing_return_button = ttk.Button(
            actions,
            text="Вернуться в библиотеку",
            style="Secondary.TButton",
            command=self.show_library,
            state="disabled",
        )
        self._processing_return_button.pack(side="left")
        self._processing_cancel_button = ttk.Button(
            actions,
            text="Отменить",
            style="Secondary.TButton",
            command=self.cancel_active_processing,
        )
        self._processing_cancel_button.pack(side="left", padx=(10, 0))
        tk.Label(
            panel,
            textvariable=self._processing_diagnostic,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=720,
            justify="left",
        ).grid(row=8, column=0, sticky="w", pady=(14, 0))

        self._show_screen(screen, animated=True)
        self._start_processing_heartbeat()

    def _start_processing_heartbeat(self) -> None:
        heartbeat_id = self._processing_heartbeat_id

        def update() -> None:
            if heartbeat_id != self._processing_heartbeat_id:
                return
            now = time.monotonic()
            elapsed = max(0, int(now - self._processing_started_at))
            idle = max(0, int(now - self._processing_last_activity_at))
            elapsed_text = self._format_elapsed(elapsed)
            if self._processing_active:
                activity = "только что" if idle < 2 else f"{idle} сек. назад"
                self._processing_activity.set(
                    f"Прошло {elapsed_text} · последнее обновление {activity}"
                )
                self.after(1000, update)
            else:
                prefix = (
                    "Завершено за"
                    if self._processing_state.get() == "Готово"
                    else "Остановлено через"
                )
                self._processing_activity.set(f"{prefix} {elapsed_text}")

        update()

    @staticmethod
    def _format_elapsed(seconds: int) -> str:
        minutes, remaining = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{remaining:02d}"
        return f"{minutes:02d}:{remaining:02d}"

    def _retry_active_processing(self) -> None:
        recording = self._active_processing_recording
        kind = self._active_processing_kind
        if recording is None:
            self.show_library()
        elif kind == "local":
            self.start_local_processing(recording)
        elif kind == "package":
            self.start_context_packaging(recording)
        elif kind == "api":
            self.start_api_generation(recording)
        elif kind == "chatgpt":
            self.start_chatgpt_generation(recording)
        else:
            self.show_library()

    def _local_processing_worker(
        self,
        recording: BBBRecording,
        settings: AppSettings | None = None,
        operation_id: int | None = None,
    ) -> None:
        active_settings = settings or getattr(self, "settings", AppSettings())
        try:
            prepared = prepare_lecture(
                recording,
                model_name=active_settings.whisper_model,
                frame_interval_seconds=active_settings.frame_interval_seconds,
                enable_ocr=active_settings.ocr_enabled,
                progress=lambda percent, message: self.after(
                    0,
                    lambda percent=percent, message=message: _deliver_processing_progress(
                        self,
                        operation_id,
                        percent,
                        message,
                    ),
                ),
            )
        except LocalProcessingError as exc:
            diagnostic_path = (
                record_exception("processing.local", exc)
                if hasattr(self, "_processing_diagnostic_path")
                else None
            )
            message = str(exc)
            self.after(
                0,
                lambda message=message, diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    message,
                    diagnostic_path,
                ),
            )
        except Exception as exc:
            diagnostic_path = (
                record_exception("processing.local.unexpected", exc)
                if hasattr(self, "_processing_diagnostic_path")
                else None
            )
            self.after(
                0,
                lambda diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    "Подготовка остановлена из-за неожиданной ошибки. Исходная запись сохранена в библиотеке.",
                    diagnostic_path,
                ),
            )
        else:
            self.after(
                0,
                lambda prepared=prepared: _deliver_processing_result(
                    self,
                    operation_id,
                    self._finish_processing_success,
                    prepared,
                ),
            )

    def _context_packaging_worker(
        self,
        recording: BBBRecording,
        operation_id: int | None = None,
    ) -> None:
        try:
            package = build_context_package(
                recording,
                progress=lambda percent, message: self.after(
                    0,
                    lambda percent=percent, message=message: _deliver_processing_progress(
                        self,
                        operation_id,
                        percent,
                        message,
                    ),
                ),
            )
        except ContextPackageError as exc:
            diagnostic_path = record_exception(
                "processing.context",
                exc,
            )
            message = str(exc)
            self.after(
                0,
                lambda message=message, diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    message,
                    diagnostic_path,
                ),
            )
        except Exception as exc:
            diagnostic_path = record_exception(
                "processing.context.unexpected",
                exc,
            )
            self.after(
                0,
                lambda diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    "Не удалось собрать пакет контекста из локальных материалов.",
                    diagnostic_path,
                ),
            )
        else:
            self.after(
                0,
                lambda package=package: _deliver_processing_result(
                    self,
                    operation_id,
                    self._finish_context_package_success,
                    package,
                ),
            )

    def _finish_processing_success(self, prepared) -> None:
        self._set_processing_progress(100, "")
        ocr = "и OCR экрана" if prepared.screen_notes_path else "без OCR экрана"
        self._processing_status.set(
            f"Готово: транскрипция, {prepared.frame_count} кадров {ocr} сохранены локально."
        )
        self._mark_processing_success()
        self._enable_processing_return()

    def _finish_context_package_success(self, package: ContextPackage) -> None:
        self._set_processing_progress(100, "")
        self._processing_status.set(
            "Готово: создано 3 файла для чата — контекст Markdown, структурированные "
            f"данные и инструкция. Временных блоков: {package.timeline_block_count}."
        )
        self._mark_processing_success()
        self._enable_processing_return()

    def show_chat_provider_choice(self, recording: BBBRecording) -> None:
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)

        ttk.Button(
            screen,
            text="← К библиотеке",
            style="Secondary.TButton",
            command=self.show_library,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            screen,
            text="Как создать конспект",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))
        tk.Label(
            screen,
            text=(
                "Текстовый пакет лекции уже подготовлен локально. Выбери удобный сервис "
                "для генерации конспекта."
            ),
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 20))

        panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=24,
            pady=22,
        )
        panel.grid(row=3, column=0, sticky="ew")
        panel.grid_columnconfigure(0, weight=1)

        if self.settings.api_configured:
            api_title = f"API · {self.settings.provider_label} ({self.settings.api_model})"
            api_description = (
                "Транскрипция, слайды и OCR будут отправлены через твой API-ключ. "
                "Готовый конспект сохранится автоматически."
            )
            api_action_text = f"Создать через {self.settings.provider_label}"

            def on_api_action() -> None:
                self.show_consent_screen(recording, "api")

            api_action = on_api_action
            api_action_style = "Primary.TButton"
        else:
            api_title = "API · не настроен"
            api_description = (
                "Добавь ключ OpenAI или DeepSeek в настройках, чтобы приложение "
                "автоматически создавало и сохраняло готовый конспект."
            )
            api_action_text = "Настроить API"
            api_action = self.show_settings
            api_action_style = "Secondary.TButton"

        tk.Label(
            panel,
            text=api_title,
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=api_description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=590,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            panel,
            text=api_action_text,
            style=api_action_style,
            command=api_action,
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(20, 0))

        ttk.Separator(panel, orient="horizontal").grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=18,
        )

        tk.Label(
            panel,
            text="Личный ChatGPT",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=3, column=0, sticky="w")
        self._chatgpt_status_label = tk.Label(
            panel,
            textvariable=self._chatgpt_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=590,
            justify="left",
        )
        self._chatgpt_status_label.grid(row=4, column=0, sticky="w", pady=(4, 0))
        tk.Label(
            panel,
            textvariable=self._chatgpt_model_summary,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=590,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(4, 0))

        chatgpt_model_row = tk.Frame(panel, background=PALETTE["surface"])
        chatgpt_model_row.grid(row=6, column=0, sticky="w", pady=(10, 0))
        tk.Label(
            chatgpt_model_row,
            text="Модель",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).pack(side="left")
        self._chatgpt_model_combobox = ttk.Combobox(
            chatgpt_model_row,
            textvariable=self._settings_chatgpt_model,
            values=(self.settings.chatgpt_model,),
            state="readonly",
            width=24,
            style="Settings.TCombobox",
        )
        self._chatgpt_model_combobox.pack(side="left", padx=(10, 0))
        self._chatgpt_model_combobox.bind(
            "<<ComboboxSelected>>",
            lambda _: self._set_active_chatgpt_model(self._settings_chatgpt_model.get()),
        )
        ttk.Button(
            panel,
            textvariable=self._chatgpt_generation_action,
            style="Primary.TButton",
            command=lambda: self.show_consent_screen(recording, "chatgpt"),
        ).grid(row=3, column=1, rowspan=4, sticky="e", padx=(20, 0))

        ttk.Separator(panel, orient="horizontal").grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=18,
        )
        tk.Label(
            panel,
            text="DeepSeek Web",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=8, column=0, sticky="w")
        tk.Label(
            panel,
            text="Откроется веб-чат DeepSeek. Отправка выполняется вручную только после твоей проверки.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
        ).grid(row=9, column=0, sticky="w", pady=(4, 0))
        ttk.Button(
            panel,
            text="Выбрать DeepSeek",
            style="Secondary.TButton",
            command=lambda: self.show_consent_screen(recording, "deepseek_handoff"),
        ).grid(row=8, column=1, rowspan=2, sticky="e", padx=(20, 0))

        self._show_screen(screen, animated=True)
        self._refresh_chatgpt_account()

    def show_consent_screen(
        self,
        recording: BBBRecording,
        flow_type: str,
    ) -> None:
        from .local_pipeline import default_lecture_directory

        target = default_lecture_directory(recording)
        context_path = target / "lesson-context.md"

        provider_label = ""
        if flow_type == "api":
            provider_label = f"API {self.settings.provider_label} ({self.settings.api_model})"
        elif flow_type == "chatgpt":
            selected_model = (
                self._settings_chatgpt_model.get().strip() or self.settings.chatgpt_model
            )
            provider_label = f"Личный ChatGPT ({selected_model})"
        elif flow_type == "deepseek_handoff":
            provider_label = "DeepSeek Web"
            self._active_handoff = None
            self._active_handoff_provider = None
            try:
                self._active_handoff = prepare_deepseek_handoff(recording, directory=target)
                self._active_handoff_provider = "DeepSeek"
            except DeepSeekHandoffError as exc:
                self._handoff_status.set(str(exc))
                return

        prompt_path = target / "lesson-prompt.md"
        size_kb = 0
        char_count = 0
        limit_warning = ""
        txt = ""
        prompt_txt = ""
        if context_path.is_file():
            try:
                txt = context_path.read_text(encoding="utf-8")
            except OSError:
                pass
        if prompt_path.is_file():
            try:
                prompt_txt = prompt_path.read_text(encoding="utf-8")
            except OSError:
                pass

        total_bytes = len(txt.encode("utf-8")) + len(prompt_txt.encode("utf-8"))
        char_count = len(txt) + len(prompt_txt)
        size_kb = max(1, total_bytes // 1024)

        provider_name = (
            "chatgpt"
            if flow_type == "chatgpt"
            else "deepseek"
            if flow_type == "deepseek_handoff"
            else "openrouter"
        )
        try:
            validate_provider_context_limits(provider_name, char_count, total_bytes)
        except OutboundContextError as exc:
            limit_warning = f"Внимание: {exc}"

        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)

        ttk.Button(
            screen,
            text="← Назад к выбору",
            style="Secondary.TButton",
            command=lambda: self.show_chat_provider_choice(recording),
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            screen,
            text="Подтверждение передачи материалов",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))

        tk.Label(
            screen,
            text=(f"Лекция: «{recording.title}»\nПолучатель: {provider_label}"),
            font=self.type.body_bold,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        transmitted_panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=22,
            pady=18,
        )
        transmitted_panel.grid(row=3, column=0, sticky="ew", pady=(20, 0))

        tk.Label(
            transmitted_panel,
            text=f"Передаётся в {provider_label}:",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")

        details = [
            "• Текст транскрипции (речь лектора по временным блокам)",
            "• Текст со слайдов презентации",
            "• Текстовые заметки с экрана (OCR)",
            "• Учебный промпт (требования к формату конспекта)",
            f"• Примерный объём: ~{size_kb} КБ ({char_count:,} символов)",
        ]
        tk.Label(
            transmitted_panel,
            text="\n".join(details),
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        if limit_warning:
            tk.Label(
                transmitted_panel,
                text=limit_warning,
                font=self.type.small,
                foreground=PALETTE["danger"],
                background=PALETTE["surface"],
                justify="left",
                wraplength=620,
            ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        local_panel = tk.Frame(
            screen,
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=22,
            pady=18,
        )
        local_panel.grid(row=4, column=0, sticky="ew", pady=(12, 0))

        tk.Label(
            local_panel,
            text="Остаётся на этом компьютере (не передаётся):",
            font=self.type.subheading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")

        local_details = [
            "✓ Исходный URL BigBlueButton",
            "✓ Идентификатор встречи (meeting ID)",
            "✓ Исходные медиафайлы записи (аудио и видео)",
            "✓ Локальные пути к файлам и служебные логи",
        ]
        tk.Label(
            local_panel,
            text="\n".join(local_details),
            font=self.type.body,
            foreground=PALETTE["success"],
            background=PALETTE["surface_soft"],
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        btn_row = tk.Frame(screen, background=PALETTE["canvas"])
        btn_row.grid(row=5, column=0, sticky="w", pady=(20, 0))

        def on_confirm() -> None:
            if flow_type == "api":
                self.start_api_generation(recording)
            elif flow_type == "chatgpt":
                self.start_chatgpt_generation(recording)
            elif flow_type == "deepseek_handoff":
                self._launch_active_handoff()

        ttk.Button(
            btn_row,
            text="Передать и продолжить",
            style="Primary.TButton",
            command=on_confirm,
        ).pack(side="left")

        ttk.Button(
            btn_row,
            text="Отмена",
            style="Secondary.TButton",
            command=lambda: self.show_chat_provider_choice(recording),
        ).pack(side="left", padx=(10, 0))

        self._show_screen(screen, animated=True)

    def start_api_generation(self, recording: BBBRecording) -> None:
        if self._processing_active:
            return
        if not self.settings.api_configured:
            self._settings_status.set("Добавь API-ключ и модель, затем сохрани настройки.")
            self.show_settings()
            return

        operation_id = self._prepare_processing_state(
            recording,
            kind="api",
            message=(
                f"Готовим текстовый запрос для {self.settings.provider_label}. "
                "Аудио, видео и данные источника останутся на этом компьютере."
            ),
        )
        self.show_processing_screen(
            recording,
            heading=f"Создаём конспект через {self.settings.provider_label}",
            description=(
                "В API отправятся только транскрипция, текст слайдов и OCR. "
                "Полученный Markdown будет сохранён как lesson.md на этом компьютере."
            ),
        )
        if hasattr(self, "_job_runner"):
            self._start_api_job(recording, self.settings, operation_id)
            return
        threading.Thread(
            target=self._api_generation_worker,
            args=(recording, self.settings, operation_id),
            daemon=True,
        ).start()

    def _start_api_job(
        self,
        recording: BBBRecording,
        settings: AppSettings,
        operation_id: int,
    ) -> None:
        def task(token: CancellationToken, progress) -> ApiLessonResult:
            token.check_cancelled()
            return generate_lesson_via_api(
                recording, settings, progress=progress, cancellation_token=token
            )

        def on_event(event: JobEvent) -> None:
            def apply_event() -> None:
                if not _operation_is_current(self, operation_id):
                    return
                if event.event_type is JobEventType.PROGRESS:
                    self._set_processing_progress(event.percent, event.message)
                elif event.event_type is JobEventType.COMPLETED:
                    self._finish_api_generation_success(recording, event.result)
                elif event.event_type is JobEventType.CANCELLED:
                    self._finish_processing_cancelled()
                elif event.event_type is JobEventType.FAILED:
                    error = RuntimeError(event.error or event.message)
                    self._processing_diagnostic_path = record_exception("generation.api", error)
                    self._finish_processing_error(
                        event.error or "Не удалось создать конспект через API."
                    )

            self.after(0, apply_event)

        token = CancellationToken()
        self._processing_token = token
        self._job_runner.run_job(task, on_event, token=token)

    def _api_generation_worker(
        self,
        recording: BBBRecording,
        settings: AppSettings,
        operation_id: int | None = None,
    ) -> None:
        try:
            result = generate_lesson_via_api(
                recording,
                settings,
                progress=lambda percent, message: self.after(
                    0,
                    lambda percent=percent, message=message: _deliver_processing_progress(
                        self,
                        operation_id,
                        percent,
                        message,
                    ),
                ),
                cancellation_token=getattr(self, "_processing_token", None),
            )
        except ApiGenerationError as exc:
            diagnostic_path = record_exception(
                "generation.api",
                exc,
            )
            message = str(exc)
            self.after(
                0,
                lambda message=message, diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    message,
                    diagnostic_path,
                ),
            )
        except Exception as exc:
            diagnostic_path = record_exception(
                "generation.api.unexpected",
                exc,
            )
            self.after(
                0,
                lambda diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    "Неожиданная ошибка остановила запрос к API. Локальные материалы "
                    "сохранены; повтори попытку или используй веб-чат.",
                    diagnostic_path,
                ),
            )
        else:
            self.after(
                0,
                lambda item=recording, output=result: _deliver_processing_result(
                    self,
                    operation_id,
                    self._finish_api_generation_success,
                    item,
                    output,
                ),
            )

    def _finish_api_generation_success(
        self,
        recording: BBBRecording,
        result: ApiLessonResult,
    ) -> None:
        self._set_processing_progress(100, "")
        self._processing_status.set(
            f"Конспект создан через {result.provider} ({result.model}) и сохранён "
            f"локально: {result.saved_lesson.character_count} символов."
        )
        self._mark_processing_success()
        self._enable_processing_return()
        operation_id = self._processing_operation_id
        self.after(
            350,
            lambda item=recording, operation_id=operation_id: (
                self.show_lesson_reader(item)
                if operation_id == self._processing_operation_id and not self._processing_active
                else None
            ),
        )

    def start_chatgpt_generation(self, recording: BBBRecording) -> None:
        if self._processing_active:
            return

        selected_model = self._settings_chatgpt_model.get().strip() or self.settings.chatgpt_model
        self._set_active_chatgpt_model(selected_model)
        account_operation_id = self._next_chatgpt_account_operation()
        operation_id = self._prepare_processing_state(
            recording,
            kind="chatgpt",
            message="Проверяем вход в личный ChatGPT и доступную модель.",
        )
        self.show_processing_screen(
            recording,
            heading="Создаём конспект через личный ChatGPT",
            description=(
                "API-ключ не используется. Текстовый пакет лекции отправляется через "
                "твой вход в ChatGPT, а готовый Markdown сохраняется локально как lesson.md."
            ),
        )
        if hasattr(self, "_job_runner"):
            self._start_chatgpt_job(recording, selected_model, operation_id, account_operation_id)
            return
        threading.Thread(
            target=self._chatgpt_generation_worker,
            args=(
                recording,
                selected_model,
                operation_id,
                account_operation_id,
            ),
            daemon=True,
        ).start()

    def _start_chatgpt_job(
        self,
        recording: BBBRecording,
        model: str,
        operation_id: int,
        account_operation_id: int,
    ) -> None:
        def task(
            token: CancellationToken, progress
        ) -> tuple[ChatGPTGenerationResult, ChatGPTAccountStatus, list[ChatGPTModel], str]:
            token.check_cancelled()
            progress(10, "Проверяем вход в ChatGPT…")
            status = chatgpt_account_status()
            if not status.signed_in:
                progress(
                    20, "Заверши вход в открывшемся окне — генерация продолжится автоматически."
                )
                status = login_with_chatgpt()
            if not status.signed_in:
                raise ChatGPTAccountError(
                    "Вход в ChatGPT не завершён. Повтори попытку и закончи авторизацию."
                )
            models: list[ChatGPTModel] = []
            model_error = ""
            try:
                models = list_chatgpt_models()
            except ChatGPTAccountError as exc:
                model_error = str(exc)
            progress(45, f"Готовим запрос для модели {model}…")
            result = generate_lesson_with_chatgpt(
                recording,
                model,
                progress=lambda percent, message: progress(
                    min(95, 45 + max(0, min(100, percent)) // 2), message
                ),
                cancellation_token=token,
            )
            return result, status, models, model_error

        def on_event(event: JobEvent) -> None:
            def apply_event() -> None:
                if not _operation_is_current(self, operation_id):
                    return
                if event.event_type is JobEventType.COMPLETED and isinstance(event.result, tuple):
                    result, status, models, model_error = event.result
                    self._finish_chatgpt_account_refresh(
                        account_operation_id, status, models, model_error
                    )
                    self._finish_chatgpt_generation_success(recording, result)
                elif event.event_type is JobEventType.CANCELLED:
                    self._finish_processing_cancelled()
                elif event.event_type is JobEventType.FAILED:
                    error = RuntimeError(event.error or event.message)
                    self._processing_diagnostic_path = record_exception("generation.chatgpt", error)
                    self._finish_processing_error(
                        event.error
                        or "Неожиданная ошибка остановила создание конспекта через ChatGPT."
                    )

            self.after(0, apply_event)

        token = CancellationToken()
        self._processing_token = token
        self._job_runner.run_job(task, on_event, token=token)

    def _chatgpt_generation_worker(
        self,
        recording: BBBRecording,
        model: str,
        operation_id: int | None = None,
        account_operation_id: int | None = None,
    ) -> None:
        active_account_operation = (
            account_operation_id
            if account_operation_id is not None
            else getattr(self, "_chatgpt_account_operation_id", 0)
        )

        def report_progress(percent: int, message: str) -> None:
            self.after(
                0,
                lambda percent=percent, message=message: _deliver_processing_progress(
                    self,
                    operation_id,
                    percent,
                    message,
                ),
            )

        try:
            report_progress(10, "Проверяем вход в ChatGPT…")
            status = chatgpt_account_status()
            if not status.signed_in:
                report_progress(
                    20,
                    "Заверши вход в открывшемся окне — генерация продолжится автоматически.",
                )
                status = login_with_chatgpt()
            if not status.signed_in:
                raise ChatGPTAccountError(
                    "Вход в ChatGPT не завершён. Повтори попытку и закончи авторизацию."
                )

            models: list[ChatGPTModel] = []
            model_error = ""
            try:
                models = list_chatgpt_models()
            except ChatGPTAccountError as exc:
                model_error = str(exc)
            self.after(
                0,
                lambda status=status, models=models, model_error=model_error: (
                    self._finish_chatgpt_account_refresh(
                        active_account_operation,
                        status,
                        models,
                        model_error,
                    )
                ),
            )

            report_progress(45, f"Готовим запрос для модели {model}…")

            def generation_progress(percent: int, message: str) -> None:
                mapped_percent = min(95, 45 + max(0, min(100, percent)) // 2)
                report_progress(mapped_percent, message)

            result = generate_lesson_with_chatgpt(
                recording,
                model,
                progress=generation_progress,
                cancellation_token=getattr(self, "_processing_token", None),
            )
        except ChatGPTAccountError as exc:
            diagnostic_path = record_exception("generation.chatgpt", exc)
            message = str(exc)
            self.after(
                0,
                lambda message=message, diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    message,
                    diagnostic_path,
                ),
            )
        except Exception as exc:
            diagnostic_path = record_exception(
                "generation.chatgpt.unexpected",
                exc,
            )
            self.after(
                0,
                lambda diagnostic_path=diagnostic_path: _deliver_processing_error(
                    self,
                    operation_id,
                    "Неожиданная ошибка остановила создание конспекта через ChatGPT. "
                    "Локальные материалы сохранены; повтори попытку.",
                    diagnostic_path,
                ),
            )
        else:
            self.after(
                0,
                lambda item=recording, output=result: _deliver_processing_result(
                    self,
                    operation_id,
                    self._finish_chatgpt_generation_success,
                    item,
                    output,
                ),
            )

    def _finish_chatgpt_generation_success(
        self,
        recording: BBBRecording,
        result: ChatGPTGenerationResult,
    ) -> None:
        self._set_processing_progress(100, "")
        self._processing_status.set(
            f"Конспект создан через личный ChatGPT ({result.model}) и сохранён локально: "
            f"{result.lesson_path}."
        )
        self._mark_processing_success()
        self._enable_processing_return()
        operation_id = self._processing_operation_id
        self.after(
            350,
            lambda item=recording, operation_id=operation_id: (
                self.show_lesson_reader(item)
                if operation_id == self._processing_operation_id and not self._processing_active
                else None
            ),
        )

    def show_deepseek_handoff(self, recording: BBBRecording) -> None:
        self._show_web_chat_handoff(recording)

    def _show_web_chat_handoff(self, recording: BBBRecording) -> None:
        provider = "DeepSeek"
        self._active_handoff = None
        self._active_handoff_provider = None
        try:
            handoff = prepare_deepseek_handoff(recording)
            description = (
                "Используется веб-чат DeepSeek без API. Условия доступа зависят "
                "от твоего аккаунта DeepSeek."
            )
        except DeepSeekHandoffError:
            self.show_library()
            return

        self._active_handoff = handoff
        self._active_handoff_provider = provider
        self._handoff_status.set(
            f"Когда нажмёшь кнопку ниже, приложение откроет {provider} и папку с файлом. "
            "Инструкция для создания lesson.md будет скопирована в буфер обмена."
        )

        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)

        ttk.Button(
            screen,
            text="← Выбрать другой чат",
            style="Secondary.TButton",
            command=lambda: self.show_chat_provider_choice(recording),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            screen,
            text=f"Создаём конспект в {provider}",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))

        panel = tk.Frame(
            screen,
            background=PALETTE["surface"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=24,
            pady=24,
        )
        panel.grid(row=2, column=0, sticky="nsew", pady=(20, 0))
        panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text=recording.title,
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 18))

        from .platform_services import PlatformKeyboardConventions

        shortcut = PlatformKeyboardConventions().format_shortcut("V")
        steps = (
            "1. Выбери новый или нужный существующий чат.\n"
            "2. Прикрепи lesson-context.md из открывшейся папки.\n"
            f"3. Вставь инструкцию из буфера сочетанием {shortcut} и сам отправь сообщение."
        )
        tk.Label(
            panel,
            text=steps,
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
            justify="left",
        ).grid(row=2, column=0, sticky="w")
        ttk.Button(
            panel,
            text=f"Открыть {provider} и скопировать инструкцию",
            style="Primary.TButton",
            command=lambda: self.show_consent_screen(recording, "deepseek_handoff"),
        ).grid(row=3, column=0, sticky="w", pady=(22, 0))
        ttk.Button(
            panel,
            text="Я получил ответ — вставить и сохранить",
            style="Secondary.TButton",
            command=lambda: self.show_lesson_editor(recording),
        ).grid(row=4, column=0, sticky="w", pady=(10, 0))
        tk.Label(
            panel,
            textvariable=self._handoff_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface"],
            justify="left",
            wraplength=720,
        ).grid(row=5, column=0, sticky="w", pady=(16, 0))

        self._show_screen(screen, animated=True)

    def _launch_active_handoff(self) -> None:
        handoff = self._active_handoff
        provider = self._active_handoff_provider
        if handoff is None or provider is None:
            self._handoff_status.set("Сначала выбери лекцию с готовым пакетом контекста.")
            return

        from .platform_services import PlatformKeyboardConventions

        shortcut = PlatformKeyboardConventions().format_shortcut("V")
        try:
            context_text = handoff.context_path.read_text(encoding="utf-8")
            prompt = handoff.prompt_path.read_text(encoding="utf-8").strip()
            _validate_outbound_text("context", context_text, ())
            _validate_outbound_text("prompt", prompt, ())
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update()
            launch_deepseek_handoff(handoff)
        except (DeepSeekHandoffError, OutboundContextError, OSError) as exc:
            self._handoff_status.set(str(exc))
        except tk.TclError:
            self._handoff_status.set("Не удалось скопировать инструкцию в буфер обмена.")
        else:
            self._handoff_status.set(
                f"{provider} и папка с файлом открыты. Выбери нужный чат, прикрепи "
                f"lesson-context.md, вставь {shortcut} и отправь сообщение."
            )

    def show_lesson_editor(self, recording: BBBRecording) -> None:
        self._lesson_status.set("")
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(3, weight=1)

        ttk.Button(
            screen,
            text="← К выбору чата",
            style="Secondary.TButton",
            command=lambda: self.show_chat_provider_choice(recording),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            screen,
            text="Сохрани готовый конспект",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))
        tk.Label(
            screen,
            text=(
                "Вставь полный ответ из ChatGPT или DeepSeek. Он сохранится локально "
                "как lesson.md и останется привязан к этой лекции."
            ),
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=780,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(6, 16))

        editor = scrolledtext.ScrolledText(
            screen,
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
            insertbackground=PALETTE["ink"],
            relief="solid",
            borderwidth=1,
            wrap="word",
            padx=18,
            pady=16,
            undo=True,
        )
        editor.grid(row=3, column=0, sticky="nsew")
        try:
            existing = read_generated_lesson(recording)
        except LessonOutputError:
            existing = ""
        if existing:
            editor.insert("1.0", existing)
        self._lesson_editor = editor

        actions = tk.Frame(screen, background=PALETTE["canvas"])
        actions.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        ttk.Button(
            actions,
            text="Сохранить lesson.md",
            style="Primary.TButton",
            command=lambda: self._save_lesson_from_editor(recording),
        ).pack(side="left")
        tk.Label(
            actions,
            textvariable=self._lesson_status,
            font=self.type.small,
            foreground=PALETTE["danger"],
            background=PALETTE["canvas"],
            wraplength=520,
            justify="left",
        ).pack(side="left", padx=(16, 0))

        self._show_screen(screen, animated=True)

    def _save_lesson_from_editor(self, recording: BBBRecording) -> None:
        editor = self._lesson_editor
        if editor is None or not editor.winfo_exists():
            self._lesson_status.set("Поле для конспекта недоступно. Открой его снова.")
            return

        try:
            saved = save_generated_lesson(recording, editor.get("1.0", "end-1c"))
        except LessonOutputError as exc:
            self._lesson_status.set(str(exc))
            return

        self._lesson_status.set(
            f"Сохранено локально: lesson.md ({saved.character_count} символов)."
        )
        self.after(180, lambda: self.show_lesson_reader(recording))

    def show_lesson_reader(self, recording: BBBRecording) -> None:
        try:
            content = read_generated_lesson(recording)
        except LessonOutputError:
            self.show_lesson_editor(recording)
            return

        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(36, 28, 36, 36))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(3, weight=1)

        ttk.Button(
            screen,
            text="← К библиотеке",
            style="Secondary.TButton",
            command=self.show_library,
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            screen,
            text="Готовый конспект",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(24, 0))
        tk.Label(
            screen,
            text=recording.title,
            font=self.type.body_bold,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", pady=(6, 16))

        reader_area = tk.Frame(screen, background=PALETTE["canvas"])
        reader_area.grid(row=3, column=0, sticky="nsew")
        reader_area.grid_rowconfigure(0, weight=1)
        reader_area.grid_columnconfigure(1, weight=1)
        toc_list = tk.Listbox(
            reader_area,
            width=28,
            exportselection=False,
            background=PALETTE["surface_soft"],
            foreground=PALETTE["ink"],
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=self.type.small,
        )
        toc_list.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        reader = scrolledtext.ScrolledText(
            reader_area,
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface"],
            relief="solid",
            borderwidth=1,
            wrap="word",
            padx=20,
            pady=18,
        )
        reader.grid(row=0, column=1, sticky="nsew")
        reader.insert("1.0", content)
        rec_key = recording.lecture_id or recording.meeting_id
        saved_pos = self._reading_positions.get(rec_key)
        if saved_pos is not None:
            reader.after(50, lambda: reader.yview_moveto(saved_pos))

        toc_entries = extract_table_of_contents(content)
        for entry in toc_entries:
            toc_list.insert("end", f"{'  ' * (entry.level - 1)}{entry.title}")

        def jump_to_toc(_: tk.Event | None = None) -> None:
            selection = toc_list.curselection()
            if selection:
                reader.see(f"{toc_entries[selection[0]].line_number}.0")
                reader.mark_set("insert", f"{toc_entries[selection[0]].line_number}.0")

        toc_list.bind("<<ListboxSelect>>", jump_to_toc)
        timestamp_lines = extract_timestamps(content)
        reader.tag_configure("timestamp", foreground=PALETTE["primary"], underline=True)
        for timestamp in timestamp_lines:
            reader.tag_add(
                "timestamp", f"{timestamp.line_number}.0", f"{timestamp.line_number}.end"
            )

        def show_timestamp(event: tk.Event) -> None:
            index = reader.index(f"@{event.x},{event.y}")
            line = int(str(index).split(".", 1)[0])
            matching = [item for item in timestamp_lines if item.line_number == line]
            if matching:
                ts = matching[0]
                self._lesson_status.set(f"Таймкод {ts.raw_str} · {int(ts.total_seconds)} сек.")
                self._open_timestamp_media_or_frame(recording, ts.total_seconds)

        reader.tag_bind("timestamp", "<Button-1>", show_timestamp)

        def update_reader_position(_: tk.Event | None = None) -> None:
            try:
                self._reading_positions[rec_key] = reader.yview()[0]
            except Exception:
                pass
            current_line = int(str(reader.index("insert")).split(".", 1)[0])
            total_lines = int(str(reader.index("end-1c")).split(".", 1)[0])
            self._lesson_status.set(f"Позиция: строка {current_line} из {total_lines}")

        reader.bind("<ButtonRelease-1>", update_reader_position, add="+")
        reader.bind("<KeyRelease>", update_reader_position, add="+")
        reader.bind("<MouseWheel>", update_reader_position, add="+")
        actions = tk.Frame(screen, background=PALETTE["canvas"])
        actions.grid(row=4, column=0, sticky="ew", pady=(16, 0))

        ttk.Button(
            actions,
            text="Изменить конспект",
            style="Secondary.TButton",
            command=lambda: self.show_lesson_editor(recording),
        ).pack(side="left")

        def export_html() -> None:
            from tkinter import filedialog

            from .lesson_export import export_lesson_to_html_file

            dest = filedialog.asksaveasfilename(
                title="Экспорт конспекта в HTML",
                defaultextension=".html",
                filetypes=[("HTML страницы", "*.html")],
                initialfile=f"{recording.title[:40]}.html",
            )
            if dest:
                export_lesson_to_html_file(recording.title, content, Path(dest))

        def export_zip() -> None:
            from tkinter import filedialog

            from .library_manager import export_lecture_archive
            from .local_pipeline import default_lecture_directory

            dest = filedialog.asksaveasfilename(
                title="Экспорт чистого архива лекции",
                defaultextension=".zip",
                filetypes=[("ZIP архивы", "*.zip")],
                initialfile=f"{recording.title[:40]}.zip",
            )
            if dest:
                export_lecture_archive(default_lecture_directory(recording), Path(dest))

        ttk.Button(
            actions,
            text="Экспорт в HTML",
            style="Secondary.TButton",
            command=export_html,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            actions,
            text="Экспорт архива (.zip)",
            style="Secondary.TButton",
            command=export_zip,
        ).pack(side="left", padx=(8, 0))

        search_var = tk.StringVar()
        search_entry = ttk.Entry(actions, textvariable=search_var, width=20, style="Source.TEntry")
        search_entry.pack(side="left", padx=(16, 0))

        def find_next() -> None:
            query = search_var.get().strip()
            if not query:
                return
            start = reader.index("insert")
            found = reader.search(query, start, nocase=True, stopindex="end")
            if not found:
                found = reader.search(query, "1.0", nocase=True, stopindex=start)
            if found:
                reader.tag_remove("search", "1.0", "end")
                reader.tag_add("search", found, f"{found}+{len(query)}c")
                reader.tag_configure("search", background="#FFF3B0")
                reader.see(found)
                reader.mark_set("insert", found)

        ttk.Button(actions, text="Найти", style="Secondary.TButton", command=find_next).pack(
            side="left", padx=(6, 0)
        )

        def export_pdf() -> None:
            from tkinter import filedialog, messagebox

            from .lesson_export import export_lesson_to_pdf_file

            dest = filedialog.asksaveasfilename(
                title="Экспорт конспекта в PDF",
                defaultextension=".pdf",
                filetypes=[("PDF документы", "*.pdf")],
                initialfile=f"{recording.title[:40]}.pdf",
            )
            if not dest:
                return
            try:
                export_lesson_to_pdf_file(recording.title, content, Path(dest))
            except RuntimeError as exc:
                messagebox.showerror("Экспорт PDF", str(exc))

        ttk.Button(
            actions, text="Экспорт в PDF", style="Secondary.TButton", command=export_pdf
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            actions,
            textvariable=self._lesson_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).pack(side="left", padx=(12, 0))

        self._show_screen(screen, animated=True)

    def _open_timestamp_media_or_frame(self, recording: BBBRecording, total_seconds: float) -> None:
        from .platform_services import PlatformSystemActions

        lec_dir = default_lecture_directory(recording, base_dir=self.app_paths.data_dir)
        frames_dir = lec_dir / "frames"
        if frames_dir.is_dir():
            frames = sorted(frames_dir.glob("frame-*.jpg"))
            if frames:
                interval = 60
                interval_path = frames_dir / "interval-seconds.txt"
                if interval_path.is_file():
                    try:
                        interval = int(interval_path.read_text(encoding="ascii").strip())
                    except Exception:
                        pass
                idx = min(len(frames) - 1, max(0, int(round(total_seconds / max(1, interval)))))
                PlatformSystemActions().open_in_file_manager(frames[idx])
                return

        audio_file = lec_dir / "audio.mp4"
        if audio_file.is_file():
            PlatformSystemActions().open_in_file_manager(audio_file)
            return

        PlatformSystemActions().open_in_file_manager(lec_dir)

    def _finish_processing_error(self, message: str) -> None:
        self._processing_active = False
        self._processing_token = None
        self._set_navigation_enabled(True)
        self._processing_state.set("Ошибка")
        self._processing_percent.set("Ошибка")
        self._processing_status.set(message)
        if self._processing_progress is not None and self._processing_progress.winfo_exists():
            self._processing_progress.stop()
            self._processing_progress.configure(
                value=0,
                style="Error.Horizontal.TProgressbar",
            )
        if (
            self._processing_status_label is not None
            and self._processing_status_label.winfo_exists()
        ):
            self._processing_status_label.configure(foreground=PALETTE["danger"])
        if (
            self._processing_percent_label is not None
            and self._processing_percent_label.winfo_exists()
        ):
            self._processing_percent_label.configure(foreground=PALETTE["danger"])
        if self._processing_diagnostic_path is not None:
            self._processing_diagnostic.set(
                f"Подробности сохранены локально: {self._processing_diagnostic_path}"
            )
        else:
            self._processing_diagnostic.set(
                "Подробный журнал создать не удалось; локальные материалы сохранены."
            )
        retry = self._processing_retry_button
        return_button = self._processing_return_button
        if retry is not None and retry.winfo_exists():
            retry.pack(
                side="left",
                before=return_button if return_button is not None else None,
                padx=(0, 10),
            )
        self._enable_processing_return()
        if self._processing_cancel_button is not None:
            self._processing_cancel_button.pack_forget()

    def _set_processing_progress(self, percent: int, message: str) -> None:
        """Show honest stage progress instead of estimating an unreliable duration."""

        if not self._processing_active:
            return
        bounded = max(0, min(100, percent))
        self._processing_last_activity_at = time.monotonic()
        self._processing_state.set("Выполняется")
        self._processing_percent.set(f"{bounded}%")
        if self._processing_progress is not None and self._processing_progress.winfo_exists():
            self._processing_progress.configure(
                value=bounded,
                style="Processing.Horizontal.TProgressbar",
            )
        if (
            self._processing_status_label is not None
            and self._processing_status_label.winfo_exists()
        ):
            self._processing_status_label.configure(foreground=PALETTE["muted"])
        if (
            self._processing_percent_label is not None
            and self._processing_percent_label.winfo_exists()
        ):
            self._processing_percent_label.configure(foreground=PALETTE["ink"])
        if message:
            self._processing_status.set(message)

    def _mark_processing_success(self) -> None:
        self._processing_active = False
        self._processing_token = None
        self._set_navigation_enabled(True)
        self._processing_state.set("Готово")
        self._processing_percent.set("100%")
        self._processing_diagnostic.set("")
        if (
            self._processing_status_label is not None
            and self._processing_status_label.winfo_exists()
        ):
            self._processing_status_label.configure(foreground=PALETTE["success"])
        retry = self._processing_retry_button
        if retry is not None and retry.winfo_exists():
            retry.pack_forget()
        if self._processing_cancel_button is not None:
            self._processing_cancel_button.pack_forget()

    def _enable_processing_return(self) -> None:
        if (
            self._processing_return_button is not None
            and self._processing_return_button.winfo_exists()
        ):
            self._processing_return_button.state(["!disabled"])

    def _show_screen(self, screen: ttk.Frame, animated: bool) -> None:
        previous = self._current_screen
        self._current_screen = screen
        screen.place(x=0, y=0, relwidth=1, relheight=1)

        if previous is None:
            return
        if not animated or self._reduce_motion():
            previous.destroy()
            return

        duration_ms = 180
        frames = 9

        def advance(frame: int = 0) -> None:
            progress = min(frame / frames, 1)
            # A short cross-slide makes navigation clear without delaying work.
            screen.place_configure(x=round((1 - progress) * 18))
            previous.place_configure(x=-round(progress * 12))
            if progress < 1:
                self.after(duration_ms // frames, lambda: advance(frame + 1))
            else:
                screen.place_configure(x=0)
                previous.destroy()

        advance()

    @staticmethod
    def _reduce_motion() -> bool:
        # Tk does not expose the Windows accessibility preference portably.
        # A command-line escape hatch keeps motion optional for now.
        return "--reduce-motion" in sys.argv


def main() -> None:
    app = StudyApp()
    if "--smoke-test-gui" in sys.argv:
        app.after(500, app.destroy)
        app.mainloop()
        sys.stdout.write("GUI smoke test passed\n")
        return
    app.mainloop()


if __name__ == "__main__":
    main()
