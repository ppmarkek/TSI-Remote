#!/usr/bin/env python3
"""Windows interface for importing and preparing local lecture-study materials."""

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
    format_imported_at,
    inspect_bbb_recording,
    load_library,
    save_to_library,
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
from .lesson_output import (
    LessonOutputError,
    lesson_is_ready,
    read_generated_lesson,
    save_generated_lesson,
)
from .local_pipeline import LocalProcessingError, lecture_is_prepared, prepare_lecture
from .settings import (
    AppSettings,
    SettingsError,
    default_model_for_provider,
    load_settings,
    save_settings,
)

PALETTE = {
    "canvas": "#FFFFFF",
    "sidebar": "#F3F6F4",
    "surface": "#FFFFFF",
    "surface_soft": "#F7FAF8",
    "ink": "#17211D",
    "muted": "#55635C",
    "faint": "#7B8981",
    "line": "#DDE5E0",
    "primary": "#176B45",
    "primary_hover": "#105A39",
    "primary_pressed": "#0B482D",
    "primary_soft": "#E8F3EC",
    "focus": "#0D7A4A",
    "success": "#176B45",
    "danger": "#A43D31",
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
    body: tuple[str, int]
    body_bold: tuple[str, int, str]
    small: tuple[str, int]


class StudyApp(tk.Tk):
    """A calm desktop shell for managing study lectures."""

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
        from .platform_services import PlatformAppPaths, migrate_legacy_data

        self.app_paths = PlatformAppPaths()
        try:
            migrate_legacy_data(self.app_paths)
        except Exception:
            pass
        self.settings, self._settings_load_warning = self._load_settings_safely()
        self.library: list[BBBRecording] = self._load_library_safely()
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
        if "Segoe UI Variable Text" in available:
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
            title=(family, 27, "bold"),
            heading=(family, 18, "bold"),
            body=(family, 11),
            body_bold=(family, 11, "bold"),
            small=(family, 9),
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
            padding=(18, 11),
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#E1E9E4"),
                ("pressed", PALETTE["primary_pressed"]),
                ("active", PALETTE["primary_hover"]),
            ],
            foreground=[("disabled", "#C8D9CF")],
        )
        self.style.configure(
            "Secondary.TButton",
            background=PALETTE["surface"],
            foreground=PALETTE["ink"],
            borderwidth=1,
            relief="solid",
            focuscolor=PALETTE["focus"],
            font=self.type.body_bold,
            padding=(16, 10),
        )
        self.style.map(
            "Secondary.TButton",
            background=[
                ("disabled", "#F4F7F5"),
                ("active", PALETTE["surface_soft"]),
            ],
            foreground=[("disabled", PALETTE["faint"])],
        )
        self.style.configure(
            "Nav.TButton",
            background=PALETTE["sidebar"],
            foreground=PALETTE["ink"],
            borderwidth=0,
            font=self.type.body_bold,
            anchor="w",
            padding=(14, 10),
        )
        self.style.map(
            "Nav.TButton",
            background=[("active", PALETTE["primary_soft"])],
        )
        self.style.configure(
            "Source.TEntry",
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            lightcolor=PALETTE["line"],
            darkcolor=PALETTE["line"],
            insertcolor=PALETTE["ink"],
            padding=(12, 10),
            font=self.type.body,
        )
        self.style.map(
            "Source.TEntry",
            bordercolor=[("focus", PALETTE["focus"])],
            lightcolor=[("focus", PALETTE["focus"])],
            darkcolor=[("focus", PALETTE["focus"])],
        )
        self.style.configure(
            "Processing.Horizontal.TProgressbar",
            troughcolor="#DCE8E0",
            background=PALETTE["primary"],
            lightcolor=PALETTE["primary"],
            darkcolor=PALETTE["primary"],
            bordercolor="#DCE8E0",
            thickness=9,
        )
        self.style.configure(
            "Error.Horizontal.TProgressbar",
            troughcolor="#F2DEDA",
            background=PALETTE["danger"],
            lightcolor=PALETTE["danger"],
            darkcolor=PALETTE["danger"],
            bordercolor="#F2DEDA",
            thickness=9,
        )
        self.style.configure(
            "TRadiobutton",
            background=PALETTE["canvas"],
            foreground=PALETTE["ink"],
            font=self.type.body,
        )
        self.style.map(
            "TRadiobutton",
            background=[("active", PALETTE["canvas"])],
        )
        self.style.configure(
            "TCheckbutton",
            background=PALETTE["canvas"],
            foreground=PALETTE["ink"],
            font=self.type.body,
        )
        self.style.map(
            "TCheckbutton",
            background=[("active", PALETTE["canvas"])],
        )
        self.style.configure(
            "Settings.TCombobox",
            fieldbackground=PALETTE["surface"],
            foreground=PALETTE["ink"],
            padding=(10, 8),
            font=self.type.body,
        )

    def _build_shell(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = tk.Frame(
            self,
            background=PALETTE["sidebar"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            width=248,
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        brand = tk.Frame(sidebar, background=PALETTE["sidebar"])
        brand.pack(fill="x", padx=26, pady=(30, 36))
        if self._sidebar_icon is not None:
            tk.Label(
                brand,
                image=self._sidebar_icon,
                background=PALETTE["sidebar"],
            ).pack(side="left")
        else:
            tk.Label(
                brand,
                text="K",
                font=(self.type.family, 14, "bold"),
                foreground="#FFFFFF",
                background=PALETTE["primary"],
                width=2,
                pady=3,
            ).pack(side="left")
        tk.Label(
            brand,
            text="Конспект",
            font=(self.type.family, 16, "bold"),
            foreground=PALETTE["ink"],
            background=PALETTE["sidebar"],
        ).pack(side="left", padx=(10, 0), pady=(2, 0))

        lectures_button = ttk.Button(
            sidebar,
            text="Лекции",
            style="Nav.TButton",
            command=self.show_library,
        )
        lectures_button.pack(fill="x", padx=14)
        settings_button = ttk.Button(
            sidebar,
            text="Настройки",
            style="Nav.TButton",
            command=self.show_settings,
        )
        settings_button.pack(fill="x", padx=14, pady=(4, 0))
        self._navigation_buttons.extend((lectures_button, settings_button))

        footer = tk.Frame(sidebar, background=PALETTE["sidebar"])
        footer.pack(side="bottom", fill="x", padx=26, pady=28)
        tk.Label(
            footer,
            text="Данные хранятся\nна этом компьютере",
            justify="left",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["sidebar"],
        ).pack(anchor="w")

        workspace = tk.Frame(self, background=PALETTE["canvas"])
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(1, weight=1)
        self.workspace = workspace

        header = tk.Frame(workspace, background=PALETTE["canvas"], height=78)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(
            header,
            text="Учебные материалы",
            font=self.type.body_bold,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).pack(side="left", padx=40, pady=28)
        tk.Label(
            header,
            text="Обработка записи — локально",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["primary_soft"],
            padx=10,
            pady=5,
        ).pack(side="right", padx=40, pady=21)

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
        screen.configure(padding=(40, 28, 40, 40))
        screen.grid_columnconfigure(0, weight=1)
        screen.grid_rowconfigure(2, weight=1)

        tk.Label(
            screen,
            text="Настройки",
            font=self.type.title,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, sticky="w")
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
        ).grid(row=1, column=0, sticky="w", pady=(8, 26))

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
            font=self.type.body_bold,
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
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 18))

        tk.Label(
            form,
            text="Провайдер",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", padx=(0, 26), pady=8)
        providers = tk.Frame(form, background=PALETTE["canvas"])
        providers.grid(row=2, column=1, sticky="w", pady=8)
        ttk.Radiobutton(
            providers,
            text="OpenAI",
            value="openai",
            variable=self._settings_provider,
            command=self._settings_provider_changed,
        ).pack(side="left")
        ttk.Radiobutton(
            providers,
            text="DeepSeek",
            value="deepseek",
            variable=self._settings_provider,
            command=self._settings_provider_changed,
        ).pack(side="left", padx=(20, 0))

        tk.Label(
            form,
            text="API-ключ",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=3, column=0, sticky="w", padx=(0, 26), pady=8)
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
        ).grid(row=4, column=0, sticky="w", padx=(0, 26), pady=8)
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
            pady=24,
        )
        tk.Label(
            form,
            text="Личный ChatGPT",
            font=self.type.body_bold,
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
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 18))

        tk.Label(
            form,
            text="Статус",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=8, column=0, sticky="w", padx=(0, 26), pady=8)
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
        ).grid(row=9, column=0, sticky="w", padx=(0, 26), pady=8)
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
            pady=24,
        )
        tk.Label(
            form,
            text="Локальная обработка записи",
            font=self.type.body_bold,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
        ).grid(row=12, column=0, columnspan=2, sticky="w")
        tk.Label(
            form,
            text="Эти параметры влияют на скорость и детализацию следующей подготовки лекции.",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=13, column=0, columnspan=2, sticky="w", pady=(6, 18))

        tk.Label(
            form,
            text="Whisper",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=14, column=0, sticky="w", padx=(0, 26), pady=8)
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
        ).grid(row=15, column=0, sticky="w", padx=(0, 26), pady=8)
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
        actions.grid(row=1, column=0, sticky="ew", pady=(28, 12))
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
            message = f"Сохранено. API {proposed.provider_label} подключён; ключ защищён Windows."
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
        threading.Thread(
            target=self._chatgpt_account_worker,
            args=(True, operation_id),
            daemon=True,
        ).start()

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
        screen.configure(padding=(40, 28, 40, 40))
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
            text="Все записи, конспекты и материалы по лекциям будут собраны здесь.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(
            intro,
            text="Новая лекция  →",
            style="Primary.TButton",
            command=self.show_new_lecture,
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        divider = tk.Frame(screen, background=PALETTE["line"], height=1)
        divider.grid(row=1, column=0, sticky="ew", pady=(32, 0))

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
        icon = tk.Canvas(
            message,
            width=56,
            height=56,
            background=PALETTE["canvas"],
            highlightthickness=0,
        )
        icon.create_rectangle(
            13,
            10,
            43,
            46,
            outline=PALETTE["primary"],
            width=2,
        )
        icon.create_line(19, 21, 37, 21, fill=PALETTE["primary"], width=2)
        icon.create_line(19, 28, 37, 28, fill=PALETTE["primary"], width=2)
        icon.create_line(19, 35, 31, 35, fill=PALETTE["primary"], width=2)
        icon.pack(pady=(0, 18))
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
        listing.grid(row=2, column=0, sticky="nsew", pady=(22, 0))
        listing.grid_columnconfigure(0, weight=1)
        listing.grid_rowconfigure(1, weight=1)

        tk.Label(
            listing,
            text=f"В библиотеке: {len(self.library)}",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        canvas = tk.Canvas(
            listing,
            background=PALETTE["canvas"],
            highlightthickness=0,
            takefocus=True,
        )
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(10, 0))
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

        for index, recording in enumerate(self.library):
            row = tk.Frame(
                rows,
                background=PALETTE["surface_soft"],
                highlightbackground=PALETTE["line"],
                highlightthickness=1,
                padx=18,
                pady=15,
            )
            row.grid(row=index, column=0, sticky="ew", pady=(0, 8))
            row.grid_columnconfigure(0, weight=1)
            tk.Label(
                row,
                text=recording.title,
                font=self.type.body_bold,
                foreground=PALETTE["ink"],
                background=PALETTE["surface_soft"],
                wraplength=580,
                justify="left",
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                row,
                text=format_imported_at(recording.imported_at),
                font=self.type.small,
                foreground=PALETTE["muted"],
                background=PALETTE["surface_soft"],
            ).grid(row=1, column=0, sticky="w", pady=(5, 0))
            tk.Label(
                row,
                text=self._recording_summary(recording),
                font=self.type.small,
                foreground=PALETTE["muted"],
                background=PALETTE["surface_soft"],
            ).grid(row=2, column=0, sticky="w", pady=(4, 0))
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
                status_text = "Материалы ещё не подготовлены"
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
                status_text = "Готово к созданию конспекта"
                status_color = PALETTE["success"]
                action_text = "Создать конспект"
                action_style = "Primary.TButton"

                def on_action_choice(item=recording) -> None:
                    self.show_chat_provider_choice(item)

                action = on_action_choice
                action_state = "normal"
            tk.Label(
                row,
                text=status_text,
                font=self.type.small,
                foreground=status_color,
                background=PALETTE["surface_soft"],
            ).grid(row=3, column=0, sticky="w", pady=(7, 0))
            ttk.Button(
                row,
                text=action_text,
                style=action_style,
                command=action,
                state=action_state,
            ).grid(row=0, column=1, rowspan=4, sticky="e", padx=(18, 0))

        self._bind_mousewheel_tree(canvas, canvas)

    def show_new_lecture(self) -> None:
        self._import_status.set("")
        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(40, 28, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))
        tk.Label(
            screen,
            text="Вставь публичную ссылку на запись BigBlueButton. Мы быстро проверим доступные материалы.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", pady=(8, 26))

        choices = tk.Frame(
            screen,
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=24,
            pady=24,
        )
        choices.grid(row=3, column=0, sticky="ew")
        choices.grid_columnconfigure(1, weight=1)
        tk.Label(
            choices,
            text="Источник записи",
            font=self.type.body_bold,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(
            choices,
            text="Видео не будет скачиваться сейчас: сначала сохраним потоки и тексты слайдов в библиотеку.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 20))
        tk.Label(
            choices,
            text="Ссылка на BBB playback",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 7))
        source_entry = ttk.Entry(
            choices,
            textvariable=self._bbb_url,
            style="Source.TEntry",
        )
        source_entry.grid(row=3, column=0, columnspan=2, sticky="ew")
        source_entry.focus_set()
        source_entry.bind("<Return>", lambda _: self.start_bbb_import())

        self._import_button = ttk.Button(
            choices,
            text="Проверить и добавить",
            style="Primary.TButton",
            command=self.start_bbb_import,
        )
        self._import_button.grid(row=4, column=0, sticky="w", pady=(16, 0))
        ttk.Button(
            choices,
            text="Выбрать аудио/видео с диска",
            style="Secondary.TButton",
            command=self.start_local_media_picker,
        ).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(16, 0))
        self._import_status_label = tk.Label(
            choices,
            textvariable=self._import_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=700,
            justify="left",
        )
        self._import_status_label.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(14, 0),
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
        threading.Thread(
            target=self._local_processing_worker,
            args=(recording, self.settings, operation_id),
            daemon=True,
        ).start()

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
        threading.Thread(
            target=self._context_packaging_worker,
            args=(recording, operation_id),
            daemon=True,
        ).start()

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
        screen.configure(padding=(40, 40, 40, 40))
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
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=28,
            pady=28,
        )
        panel.grid(row=1, column=0, sticky="ew", pady=(26, 0))
        panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text=recording.title,
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=(f"{format_imported_at(recording.imported_at)} · ID …{recording.meeting_id[-8:]}"),
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        tk.Label(
            panel,
            text=description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=700,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(12, 22))
        progress_header = tk.Frame(panel, background=PALETTE["surface_soft"])
        progress_header.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        progress_header.grid_columnconfigure(0, weight=1)
        tk.Label(
            progress_header,
            textvariable=self._processing_state,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")
        self._processing_percent_label = tk.Label(
            progress_header,
            textvariable=self._processing_percent,
            font=self.type.small,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
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
            background=PALETTE["surface_soft"],
            justify="left",
            wraplength=720,
        )
        self._processing_status_label.grid(row=5, column=0, sticky="w", pady=(18, 0))
        tk.Label(
            panel,
            textvariable=self._processing_activity,
            font=self.type.small,
            foreground=PALETTE["faint"],
            background=PALETTE["surface_soft"],
        ).grid(row=6, column=0, sticky="w", pady=(7, 18))

        actions = tk.Frame(panel, background=PALETTE["surface_soft"])
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
        tk.Label(
            panel,
            textvariable=self._processing_diagnostic,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
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
        screen.configure(padding=(40, 32, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))
        tk.Label(
            screen,
            text=(
                "Текстовый пакет лекции уже подготовлен локально. Можно создать "
                "lesson.md автоматически через личный ChatGPT или API. DeepSeek остаётся "
                "доступен как ручной веб-чат."
            ),
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(8, 24))

        panel = tk.Frame(
            screen,
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=28,
            pady=26,
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
                "Добавь ключ OpenAI или DeepSeek, чтобы приложение само создавало "
                "и сохраняло готовый конспект."
            )
            api_action_text = "Настроить API"
            api_action = self.show_settings
            api_action_style = "Secondary.TButton"

        tk.Label(
            panel,
            text=api_title,
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=api_description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=590,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
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
            pady=22,
        )

        tk.Label(
            panel,
            text="Личный ChatGPT",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=3, column=0, sticky="w")
        self._chatgpt_status_label = tk.Label(
            panel,
            textvariable=self._chatgpt_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=590,
            justify="left",
        )
        self._chatgpt_status_label.grid(row=4, column=0, sticky="w", pady=(5, 0))
        tk.Label(
            panel,
            textvariable=self._chatgpt_model_summary,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=590,
            justify="left",
        ).grid(row=5, column=0, sticky="w", pady=(5, 0))

        chatgpt_model_row = tk.Frame(panel, background=PALETTE["surface_soft"])
        chatgpt_model_row.grid(row=6, column=0, sticky="w", pady=(14, 0))
        tk.Label(
            chatgpt_model_row,
            text="Модель",
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).pack(side="left")
        self._chatgpt_model_combobox = ttk.Combobox(
            chatgpt_model_row,
            textvariable=self._settings_chatgpt_model,
            values=(self.settings.chatgpt_model,),
            state="readonly",
            width=24,
            style="Settings.TCombobox",
        )
        self._chatgpt_model_combobox.pack(side="left", padx=(12, 0))
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
            pady=22,
        )
        tk.Label(
            panel,
            text="DeepSeek",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=8, column=0, sticky="w")
        tk.Label(
            panel,
            text="Откроется веб-чат DeepSeek. Отправка выполняется только после твоей проверки.",
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
        ).grid(row=9, column=0, sticky="w", pady=(5, 0))
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
            try:
                self._active_handoff = prepare_deepseek_handoff(recording, target)
                self._active_handoff_provider = "DeepSeek"
            except Exception:
                pass

        size_kb = 0
        char_count = 0
        if context_path.is_file():
            try:
                txt = context_path.read_text(encoding="utf-8")
                size_kb = max(1, len(txt.encode("utf-8")) // 1024)
                char_count = len(txt)
            except OSError:
                pass

        screen = ttk.Frame(self.content, style="TFrame")
        screen.configure(padding=(40, 32, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))

        tk.Label(
            screen,
            text=(f"Лекция: «{recording.title}»\nПолучатель: {provider_label}"),
            font=self.type.body_bold,
            foreground=PALETTE["ink"],
            background=PALETTE["canvas"],
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(12, 0))

        panel = tk.Frame(
            screen,
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=28,
            pady=24,
        )
        panel.grid(row=3, column=0, sticky="nsew", pady=(24, 0))

        tk.Label(
            panel,
            text="Состав передаваемых данных:",
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")

        details = [
            "• Текст транскрипции (речь лектора по временным блокам)",
            "• Текст со слайдов презентации",
            "• Текстовые заметки с экрана (OCR)",
            "• Учебный промпт (требования к формату конспекта)",
            f"• Примерный объём: ~{size_kb} КБ ({char_count:,} символов)",
        ]
        tk.Label(
            panel,
            text="\n".join(details),
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(12, 0))

        tk.Label(
            panel,
            text=(
                "✓ Исходный URL BigBlueButton удалён.\n"
                "✓ Идентификатор встречи (meeting ID) удалён.\n"
                "✓ Локальные пути к файлам и служебные логи исключены."
            ),
            font=self.type.small,
            foreground=PALETTE["success"],
            background=PALETTE["surface_soft"],
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(16, 0))

        btn_row = tk.Frame(panel, background=PALETTE["surface_soft"])
        btn_row.grid(row=3, column=0, sticky="w", pady=(20, 0))

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
        ).pack(side="left", padx=(12, 0))

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
        threading.Thread(
            target=self._api_generation_worker,
            args=(recording, self.settings, operation_id),
            daemon=True,
        ).start()

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
        screen.configure(padding=(40, 32, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))

        panel = tk.Frame(
            screen,
            background=PALETTE["surface_soft"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=28,
            pady=28,
        )
        panel.grid(row=2, column=0, sticky="nsew", pady=(24, 0))
        panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            panel,
            text=recording.title,
            font=self.type.heading,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            panel,
            text=description,
            font=self.type.body,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 22))

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
            background=PALETTE["surface_soft"],
            justify="left",
        ).grid(row=2, column=0, sticky="w")
        ttk.Button(
            panel,
            text=f"Открыть {provider} и скопировать инструкцию",
            style="Primary.TButton",
            command=lambda: self.show_consent_screen(recording, "deepseek_handoff"),
        ).grid(row=3, column=0, sticky="w", pady=(26, 0))
        ttk.Button(
            panel,
            text="Я получил ответ — вставить и сохранить",
            style="Secondary.TButton",
            command=lambda: self.show_lesson_editor(recording),
        ).grid(row=4, column=0, sticky="w", pady=(12, 0))
        tk.Label(
            panel,
            textvariable=self._handoff_status,
            font=self.type.small,
            foreground=PALETTE["muted"],
            background=PALETTE["surface_soft"],
            justify="left",
            wraplength=720,
        ).grid(row=5, column=0, sticky="w", pady=(18, 0))

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
            prompt = handoff.prompt_path.read_text(encoding="utf-8").strip()
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update()
            launch_deepseek_handoff(handoff)
        except (DeepSeekHandoffError, OSError) as exc:
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
        screen.configure(padding=(40, 32, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))
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
        ).grid(row=2, column=0, sticky="w", pady=(8, 18))

        editor = scrolledtext.ScrolledText(
            screen,
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
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
        actions.grid(row=4, column=0, sticky="ew", pady=(18, 0))
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
        screen.configure(padding=(40, 32, 40, 40))
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
        ).grid(row=1, column=0, sticky="w", pady=(34, 0))
        tk.Label(
            screen,
            text=recording.title,
            font=self.type.body_bold,
            foreground=PALETTE["muted"],
            background=PALETTE["canvas"],
        ).grid(row=2, column=0, sticky="w", pady=(8, 18))

        reader = scrolledtext.ScrolledText(
            screen,
            font=self.type.body,
            foreground=PALETTE["ink"],
            background=PALETTE["surface_soft"],
            relief="solid",
            borderwidth=1,
            wrap="word",
            padx=18,
            pady=16,
        )
        reader.grid(row=3, column=0, sticky="nsew")
        reader.insert("1.0", content)
        actions = tk.Frame(screen, background=PALETTE["canvas"])
        actions.grid(row=4, column=0, sticky="ew", pady=(18, 0))

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
                export_lecture_archive(recording, default_lecture_directory(recording), Path(dest))

        ttk.Button(
            actions,
            text="Экспорт в HTML",
            style="Secondary.TButton",
            command=export_html,
        ).pack(side="left", padx=(10, 0))

        ttk.Button(
            actions,
            text="Экспорт архива (.zip)",
            style="Secondary.TButton",
            command=export_zip,
        ).pack(side="left", padx=(10, 0))

        self._show_screen(screen, animated=True)

    def _finish_processing_error(self, message: str) -> None:
        self._processing_active = False
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
    app.mainloop()


if __name__ == "__main__":
    main()
