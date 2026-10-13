"""The Python side of the desktop UI.

pywebview exposes every public method of :class:`KonspektApi` to JavaScript as
``window.pywebview.api.<name>()``. Each call runs on its own thread and returns
``{"ok": true, "data": ...}`` or ``{"ok": false, "error": "..."}``; exceptions
never cross the bridge. Internal state uses ``_``-prefixed names so pywebview
does not expose it.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from ..bbb_import import BBBRecording
from ..diagnostics import record_exception
from ..job_runner import CancellationToken
from ..library_manager import (
    empty_trash,
    export_lecture_archive,
    format_imported_at,
    list_trash,
    load_library,
    move_to_trash,
    rename_recording,
    restore_from_trash,
    save_to_library,
)
from ..markdown_html import render_inline, render_markdown, strip_inline_markdown
from ..outbound_context import OutboundContextError, validate_provider_context_limits
from ..settings import (
    SUPPORTED_FRAME_INTERVALS,
    SUPPORTED_PROVIDERS,
    SUPPORTED_WHISPER_MODELS,
    AppSettings,
    default_model_for_provider,
    load_settings,
    save_settings,
)
from ..study_cards import StudyCard, extract_study_cards
from ..study_progress import StudyStore
from ..workflow import LectureState, resolve_lecture_state
from .tasks import ProgressCallback, Task, TaskQueue

WEB_CHAT_TARGETS = {
    "deepseek": ("DeepSeek", "https://chat.deepseek.com/"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/"),
    "gemini": ("Gemini", "https://gemini.google.com/app"),
    "claude": ("Claude", "https://claude.ai/new"),
}
MEDIA_EXTENSIONS = (".mp4", ".mp3", ".m4a", ".wav", ".mkv", ".webm", ".aac")
_AUDIO_CANDIDATES = (
    "audio.wav",
    "audio.mp4",
    "webcams.mp4",
    "webcams.webm",
)
_PLACEHOLDER_KEY = "••••••••"
_CHATGPT_STATUS_TTL = 60.0
_POLL_TIMEOUT = 20.0
_READING_WORDS_PER_MINUTE = 180


class UserFacingError(RuntimeError):
    """A message that can be shown to the user as is."""


def _bridge(method: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    """Wrap a public API method so that JavaScript always receives a result object."""

    @functools.wraps(method)
    def wrapper(self: KonspektApi, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return {"ok": True, "data": method(self, *args, **kwargs)}
        except Exception as exc:
            return {"ok": False, "error": self._error_message(f"web.{method.__name__}", exc)}

    return wrapper


class KonspektApi:
    """All actions that the HTML interface can request."""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        settings_path: Path | None = None,
        open_url: Callable[[str], Any] = webbrowser.open_new_tab,
        reveal_path: Callable[[Path], None] | None = None,
        startup_warning: str = "",
    ) -> None:
        if data_dir is None:
            from ..platform_services import PlatformAppPaths

            data_dir = PlatformAppPaths().data_dir
        self._data_dir = data_dir
        self._library_path = data_dir / "library.json"
        self._settings_path = settings_path or data_dir / "settings.json"
        self._open_url = open_url
        self._reveal = reveal_path or _reveal_in_file_manager
        self._startup_warning = startup_warning
        self._study = StudyStore(data_dir / "study.json")
        self._window: Any = None
        self._chrome_handler: Callable[[bool], None] | None = None
        self._lock = threading.RLock()
        self._settings_cache: AppSettings | None = None
        self._settings_warning = ""
        self._chatgpt_cache: tuple[float, dict[str, Any]] | None = None
        self._lesson_cache: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
        self._duration_cache: dict[Path, tuple[tuple[int, int], float]] = {}
        self._tasks = TaskQueue(
            {"prepare": self._run_prepare, "generate": self._run_generate},
            {"prepare": "local", "generate": "remote"},
            error_formatter=lambda task, exc: self._error_message(f"task.{task.kind}", exc),
            on_finished=self._task_finished,
        )

    # ================================================================ plumbing
    def _attach_window(self, window: Any) -> None:
        self._window = window

    def _set_chrome_handler(self, handler: Callable[[bool], None]) -> None:
        self._chrome_handler = handler

    def _shutdown(self) -> None:
        self._tasks.shutdown()

    def _has_active_tasks(self) -> bool:
        return self._tasks.has_active()

    def _error_message(self, area: str, exc: BaseException) -> str:
        if isinstance(exc, (UserFacingError, ValueError)) or (
            isinstance(exc, RuntimeError) and type(exc).__module__.startswith("konspekt")
        ):
            message = str(exc).strip()
            if message:
                if not isinstance(exc, UserFacingError):
                    record_exception(area, exc)
                return message
        record_exception(area, exc)
        if isinstance(exc, OSError):
            detail = exc.strerror or str(exc)
            return f"Не удалось обратиться к файлу: {detail}"
        return (
            f"Непредвиденная ошибка ({type(exc).__name__}). "
            "Подробности записаны в журнал диагностики."
        )

    def _settings(self) -> AppSettings:
        with self._lock:
            if self._settings_cache is None:
                try:
                    self._settings_cache = load_settings(self._settings_path)
                except Exception as exc:
                    record_exception("web.settings.load", exc)
                    self._settings_warning = (
                        "Настройки не удалось прочитать, используются значения по умолчанию."
                    )
                    self._settings_cache = AppSettings()
            return self._settings_cache

    def _recordings(self) -> list[BBBRecording]:
        return load_library(self._library_path) if self._library_path.is_file() else []

    def _recording(self, key: str) -> BBBRecording:
        for recording in self._recordings():
            if lecture_key(recording) == key:
                return recording
        raise UserFacingError("Лекция не найдена. Возможно, её уже удалили.")

    def _directory(self, recording: BBBRecording) -> Path:
        from ..local_pipeline import default_lecture_directory

        return default_lecture_directory(recording, base_dir=self._data_dir)

    def _lesson_text(self, directory: Path) -> str | None:
        path = directory / "lesson.md"
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except (OSError, UnicodeError):
            return None

    def _lesson_bundle(self, directory: Path) -> dict[str, Any] | None:
        """Parsed lesson (html + cards), cached until lesson.md changes."""

        path = directory / "lesson.md"
        try:
            stat = path.stat()
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._lesson_cache.get(path)
            if cached is not None and cached[0] == signature:
                return cached[1]
        text = self._lesson_text(directory)
        if text is None:
            return None
        rendered = render_markdown(text)
        bundle = {
            "text": text,
            "rendered": rendered,
            "cards": extract_study_cards(text),
        }
        with self._lock:
            self._lesson_cache[path] = (signature, bundle)
        return bundle

    def _duration(self, directory: Path) -> float:
        path = directory / "transcript.json"
        try:
            stat = path.stat()
        except OSError:
            return 0.0
        signature = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._duration_cache.get(path)
            if cached is not None and cached[0] == signature:
                return cached[1]
        duration = 0.0
        for segment in _read_transcript_segments(path):
            duration = max(duration, segment["end"])
        with self._lock:
            self._duration_cache[path] = (signature, duration)
        return duration

    def _lecture_summary(self, recording: BBBRecording) -> dict[str, Any]:
        key = lecture_key(recording)
        try:
            directory: Path | None = self._directory(recording)
        except Exception:
            directory = None
        state = LectureState.IMPORTED
        if directory is not None and directory.is_dir():
            try:
                state = resolve_lecture_state(directory)
            except OSError:
                state = LectureState.IMPORTED
        stage = {
            LectureState.LESSON_READY: "lesson",
            LectureState.PACKAGE_READY: "package",
            LectureState.PREPARED: "prepared",
        }.get(state, "imported")
        if stage == "imported" and state in {
            LectureState.RECOVERABLE_PARTIAL,
            LectureState.FAILED,
        }:
            stage = "partial"
        cards: dict[str, int] | None = None
        reading_minutes = 0
        if stage == "lesson" and directory is not None:
            bundle = self._lesson_bundle(directory)
            if bundle is not None:
                card_ids = [card.card_id for card in bundle["cards"]]
                cards = self._study.deck_stats(key, card_ids).to_dict()
                reading_minutes = max(
                    1, round(bundle["rendered"].word_count / _READING_WORDS_PER_MINUTE)
                )
        active = self._tasks.active_for(key)
        return {
            "key": key,
            "title": recording.title,
            "source": "local" if recording.source_url.startswith("local://") else "link",
            "imported_at": recording.imported_at,
            "imported_label": format_imported_at(recording.imported_at),
            "stage": stage,
            "duration": self._duration(directory) if directory is not None else 0.0,
            "has_screen": recording.has_screen_share,
            "cards": cards,
            "reading_minutes": reading_minutes,
            "reading_position": self._study.reading_position(key),
            "last_opened": self._study.last_opened(key),
            "task": active.to_dict() if active is not None else None,
        }

    def _media_path(self, key: str, kind: str, argument: str) -> Path | None:
        """Resolve ``media/<key>/<kind>/<argument>`` for the local server."""

        recording = self._recording(key)
        directory = self._directory(recording)
        if kind == "audio":
            for name in _AUDIO_CANDIDATES:
                candidate = directory / name
                if candidate.is_file() and not candidate.is_symlink():
                    return candidate
            return None
        if kind == "frame" and argument.isdigit():
            frames = _frame_files(directory)
            if not frames:
                return None
            interval = _frame_interval(directory)
            index = round(int(argument) / max(1, interval))
            return frames[min(len(frames) - 1, max(0, index))]
        return None

    def _run_prepare(self, task: Task, progress: ProgressCallback, token: CancellationToken) -> str:
        from ..context_package import build_context_package
        from ..local_pipeline import prepare_lecture

        recording = self._recording(task.lecture_key)
        directory = self._directory(recording)
        settings = self._settings()
        prepare_lecture(
            recording,
            directory=directory,
            model_name=settings.whisper_model,
            frame_interval_seconds=settings.frame_interval_seconds,
            enable_ocr=settings.ocr_enabled,
            progress=lambda percent, message: progress(int(percent * 0.9), message),
            cancellation_token=token,
        )
        token.check_cancelled()
        progress(91, "Собираем материалы для конспекта…")
        package = build_context_package(
            recording,
            directory=directory,
            progress=lambda percent, message: progress(90 + int(percent / 10), message),
            cancellation_token=token,
        )
        return f"Лекция подготовлена: {package.timeline_block_count} смысловых блоков."

    def _run_generate(
        self, task: Task, progress: ProgressCallback, token: CancellationToken
    ) -> str:
        recording = self._recording(task.lecture_key)
        directory = self._directory(recording)
        provider = str(task.params.get("provider", ""))
        model = str(task.params.get("model", "")).strip()
        self._check_outbound_size(directory, self._limit_key(provider))
        if provider == "chatgpt":
            from ..chatgpt_account import generate_lesson_with_chatgpt

            result = generate_lesson_with_chatgpt(
                recording,
                model or self._settings().chatgpt_model,
                directory=directory,
                progress=progress,
                cancellation_token=token,
            )
            return f"Конспект готов (ChatGPT, {result.model})."
        if provider == "api":
            from ..api_generation import generate_lesson_via_api

            settings = self._settings()
            if model:
                settings = replace(settings, api_model=model)
            api_result = generate_lesson_via_api(
                recording,
                settings,
                directory=directory,
                progress=progress,
                cancellation_token=token,
            )
            return f"Конспект готов ({api_result.provider}, {api_result.model})."
        raise UserFacingError("Неизвестный способ создания конспекта.")

    def _task_finished(self, task: Task) -> None:
        follow_up = task.params.get("then_generate")
        if task.kind == "prepare" and task.status == "done" and isinstance(follow_up, dict):
            self._tasks.submit(
                "generate",
                task.lecture_key,
                task.title,
                {
                    "provider": str(follow_up.get("provider", "")),
                    "model": str(follow_up.get("model", "")),
                },
            )

    def _limit_key(self, provider: str) -> str:
        if provider == "api":
            return self._settings().api_provider
        return {"manual": "deepseek_web"}.get(provider, provider)

    def _check_outbound_size(self, directory: Path, limit_key: str) -> tuple[int, int]:
        context_path = directory / "lesson-context.md"
        prompt_path = directory / "lesson-prompt.md"
        if not context_path.is_file() or not prompt_path.is_file():
            raise UserFacingError(
                "Сначала подготовь лекцию — материалы для конспекта ещё не собраны."
            )
        context = context_path.read_text(encoding="utf-8")
        prompt = prompt_path.read_text(encoding="utf-8")
        characters = len(context) + len(prompt)
        size = len(context.encode("utf-8")) + len(prompt.encode("utf-8"))
        validate_provider_context_limits(limit_key, characters, size)
        return characters, size

    def _ensure_package(self, recording: BBBRecording, directory: Path) -> None:
        if (directory / "lesson-context.md").is_file() and (
            directory / "lesson-prompt.md"
        ).is_file():
            return
        if not (directory / "transcript.json").is_file():
            raise UserFacingError("Сначала подготовь лекцию: расшифровка ещё не создана.")
        from ..context_package import build_context_package

        build_context_package(recording, directory=directory)

    def _card_payload(self, key: str, card: StudyCard) -> dict[str, Any]:
        state = self._study.card_state(key, card.card_id)
        back_html = render_markdown(card.back).html if card.back.strip() else ""
        return {
            "id": card.card_id,
            "kind": card.kind,
            "front_html": render_inline(card.front),
            "back_html": back_html,
            "section": card.section,
            "hint": card.hint,
            "timestamps": list(card.timestamps),
            "box": state.box,
            "is_new": state.is_new,
            "due": state.is_due(_today()),
        }

    def _file_dialog(
        self, kind: str, *, save_filename: str = "", file_types: tuple[str, ...] = ()
    ) -> str | None:
        if self._window is None:
            raise UserFacingError("Окно приложения ещё не готово.")
        import webview

        dialog_types = getattr(webview, "FileDialog", None)
        if dialog_types is not None:
            dialog_type = dialog_types.SAVE if kind == "save" else dialog_types.OPEN
        else:
            dialog_type = webview.SAVE_DIALOG if kind == "save" else webview.OPEN_DIALOG
        result = self._window.create_file_dialog(
            dialog_type,
            save_filename=save_filename,
            file_types=file_types,
        )
        if not result:
            return None
        if isinstance(result, (list, tuple)):
            return str(result[0]) if result else None
        return str(result)

    # ============================================================ application
    @_bridge
    def bootstrap(self) -> dict[str, Any]:
        settings = self._settings()
        warnings = [item for item in (self._startup_warning, self._settings_warning) if item]
        return {
            "version": __version__,
            "platform": sys.platform,
            "preferences": self._study.preferences(),
            "settings": _settings_payload(settings),
            "lectures": [self._lecture_summary(item) for item in self._recordings()],
            "tasks": self._tasks.snapshot(),
            "tasks_version": self._tasks.version,
            "library_version": self._tasks.library_version,
            "warnings": warnings,
        }

    @_bridge
    def poll(self, version: int = -1, library_version: int = -1) -> dict[str, Any]:
        """Long-poll: return as soon as tasks change, or after a timeout."""

        current = self._tasks.wait_for_change(int(version), _POLL_TIMEOUT)
        library_current = self._tasks.library_version
        return {
            "version": current,
            "tasks": self._tasks.snapshot(),
            "library_version": library_current,
            "lectures": [self._lecture_summary(item) for item in self._recordings()]
            if library_current != int(library_version)
            else None,
        }

    @_bridge
    def list_lectures(self) -> list[dict[str, Any]]:
        return [self._lecture_summary(item) for item in self._recordings()]

    @_bridge
    def set_preferences(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self._study.update_preferences(changes if isinstance(changes, dict) else {})

    @_bridge
    def apply_window_theme(self, dark: bool) -> None:
        """Match the native title bar to the theme the page has just applied."""

        if self._chrome_handler is not None:
            self._chrome_handler(bool(dark))

    # ================================================================= import
    @_bridge
    def add_link(self, url: str, prepare: bool = True, then_generate: Any = None) -> dict[str, Any]:
        from ..bbb_import import inspect_bbb_recording

        clean = str(url or "").strip()
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise UserFacingError("Вставь полную ссылку на запись, начиная с https://")
        recording = inspect_bbb_recording(clean)
        save_to_library(recording, path=self._library_path)
        return self._after_import(recording, prepare, then_generate)

    @_bridge
    def choose_media_file(self) -> str | None:
        patterns = ";".join(f"*{ext}" for ext in MEDIA_EXTENSIONS)
        return self._file_dialog(
            "open",
            file_types=(f"Аудио и видео ({patterns})", "Все файлы (*.*)"),
        )

    @_bridge
    def import_media(
        self,
        path: str,
        title: str = "",
        prepare: bool = True,
        then_generate: Any = None,
    ) -> dict[str, Any]:
        from ..local_media_import import import_local_media_file

        source = Path(str(path or "")).expanduser()
        if not source.is_file():
            raise UserFacingError("Файл не найден. Выбери его ещё раз.")
        recording, _directory = import_local_media_file(
            source,
            custom_title=title.strip() or None,
            library_path=self._library_path,
            base_dir=self._data_dir,
        )
        return self._after_import(recording, prepare, then_generate)

    def _after_import(
        self,
        recording: BBBRecording,
        prepare: bool,
        then_generate: Any,
    ) -> dict[str, Any]:
        key = lecture_key(recording)
        if prepare:
            self._tasks.submit("prepare", key, recording.title, _follow_up(then_generate))
        else:
            self._tasks.notify_external_change()
        return self._lecture_summary(recording)

    # ============================================================== processing
    @_bridge
    def prepare(self, key: str, then_generate: Any = None) -> dict[str, Any]:
        recording = self._recording(key)
        params = _follow_up(then_generate)
        return self._tasks.submit("prepare", key, recording.title, params).to_dict()

    @_bridge
    def generate_options(self, key: str) -> dict[str, Any]:
        recording = self._recording(key)
        directory = self._directory(recording)
        self._ensure_package(recording, directory)
        settings = self._settings()
        context = (directory / "lesson-context.md").read_text(encoding="utf-8")
        prompt = (directory / "lesson-prompt.md").read_text(encoding="utf-8")
        characters = len(context) + len(prompt)
        size = len(context.encode("utf-8")) + len(prompt.encode("utf-8"))
        providers = []
        for provider_id, label, model in (
            ("chatgpt", "ChatGPT (твой аккаунт)", settings.chatgpt_model),
            ("api", f"API {settings.provider_label}", settings.api_model),
            ("manual", "Вручную через веб-чат", ""),
        ):
            warning = ""
            try:
                validate_provider_context_limits(self._limit_key(provider_id), characters, size)
            except OutboundContextError as exc:
                warning = str(exc)
            providers.append(
                {
                    "id": provider_id,
                    "label": label,
                    "model": model,
                    "configured": settings.api_configured if provider_id == "api" else True,
                    "limit_warning": warning,
                }
            )
        return {
            "title": recording.title,
            "characters": characters,
            "size_kb": max(1, size // 1024),
            "has_lesson": (directory / "lesson.md").is_file(),
            "providers": providers,
            "sends": [
                "Расшифровка речи преподавателя по временным блокам",
                "Текст со слайдов и заметки с экрана (OCR)",
                "Учебная инструкция: каким должен быть конспект",
            ],
            "stays": [
                "Аудио и видео записи",
                "Ссылка на запись и идентификатор встречи",
                "Пути к файлам, имя пользователя и служебные журналы",
            ],
        }

    @_bridge
    def generate(self, key: str, provider: str, model: str = "") -> dict[str, Any]:
        recording = self._recording(key)
        directory = self._directory(recording)
        if provider not in {"chatgpt", "api"}:
            raise UserFacingError("Выбери, через что создать конспект.")
        if provider == "api" and not self._settings().api_configured:
            raise UserFacingError("Сначала добавь API-ключ в настройках.")
        self._ensure_package(recording, directory)
        self._check_outbound_size(directory, self._limit_key(provider))
        task = self._tasks.submit(
            "generate",
            key,
            recording.title,
            {"provider": provider, "model": str(model or "").strip()},
        )
        return task.to_dict()

    @_bridge
    def web_handoff(self, key: str) -> dict[str, Any]:
        from ..deepseek_handoff import prepare_deepseek_handoff

        recording = self._recording(key)
        directory = self._directory(recording)
        self._ensure_package(recording, directory)
        handoff = prepare_deepseek_handoff(recording, directory=directory)
        prompt = handoff.prompt_path.read_text(encoding="utf-8").strip()
        context = handoff.context_path.read_text(encoding="utf-8").strip()
        return {
            "prompt": prompt,
            "combined": f"{prompt}\n\n---\n\n{context}\n",
            "context_chars": len(context),
            "targets": [
                {"id": target_id, "label": label, "url": url}
                for target_id, (label, url) in WEB_CHAT_TARGETS.items()
            ],
        }

    @_bridge
    def reveal_context_file(self, key: str) -> None:
        directory = self._directory(self._recording(key))
        target = directory / "lesson-context.md"
        if not target.is_file():
            raise UserFacingError("Файл с материалами лекции ещё не создан.")
        self._reveal(target)

    @_bridge
    def cancel_task(self, task_id: str) -> bool:
        return self._tasks.cancel(str(task_id))

    @_bridge
    def dismiss_task(self, task_id: str) -> bool:
        return self._tasks.dismiss(str(task_id))

    # ================================================================ reading
    @_bridge
    def get_lesson(self, key: str) -> dict[str, Any]:
        recording = self._recording(key)
        directory = self._directory(recording)
        bundle = self._lesson_bundle(directory)
        if bundle is None:
            raise UserFacingError("Конспект для этой лекции ещё не создан.")
        rendered = bundle["rendered"]
        frames = _frame_files(directory)
        return {
            "key": key,
            "title": recording.title,
            "markdown": bundle["text"],
            "html": rendered.html,
            "headings": [
                {"level": item.level, "title": item.title, "anchor": item.anchor}
                for item in rendered.headings
            ],
            "word_count": rendered.word_count,
            "reading_minutes": max(1, round(rendered.word_count / _READING_WORDS_PER_MINUTE)),
            "reading_position": self._study.reading_position(key),
            "card_count": len(bundle["cards"]),
            "has_audio": self._media_path(key, "audio", "") is not None,
            "frame_count": len(frames),
            "frame_interval": _frame_interval(directory) if frames else 0,
            "duration": self._duration(directory),
            "media_base": f"media/{key}/",
        }

    @_bridge
    def save_lesson(self, key: str, markdown: str) -> dict[str, Any]:
        from ..lesson_output import save_generated_lesson

        recording = self._recording(key)
        save_generated_lesson(recording, str(markdown or ""), directory=self._directory(recording))
        self._tasks.notify_external_change()
        return {"saved": True}

    @_bridge
    def get_transcript(self, key: str) -> dict[str, Any]:
        directory = self._directory(self._recording(key))
        segments = _read_transcript_segments(directory / "transcript.json")
        if not segments:
            raise UserFacingError("Расшифровка появится после подготовки лекции.")
        return {"segments": segments}

    @_bridge
    def set_reading_position(self, key: str, position: float) -> None:
        self._study.set_reading_position(str(key), position)

    @_bridge
    def search(self, query: str) -> list[dict[str, Any]]:
        needle = str(query or "").strip().casefold()
        if len(needle) < 2:
            return []
        results: list[dict[str, Any]] = []
        total = 0
        for recording in self._recordings():
            key = lecture_key(recording)
            title_hit = needle in recording.title.casefold()
            matches: list[str] = []
            try:
                directory = self._directory(recording)
            except Exception:
                continue
            bundle = self._lesson_bundle(directory)
            if bundle is not None:
                for line in bundle["text"].splitlines():
                    plain = strip_inline_markdown(line.strip(" #>-*|\t"))
                    position = plain.casefold().find(needle)
                    if position < 0:
                        continue
                    matches.append(_snippet(plain, position, len(needle)))
                    if len(matches) >= 4:
                        break
            if title_hit or matches:
                results.append(
                    {
                        "key": key,
                        "title": recording.title,
                        "title_hit": title_hit,
                        "has_lesson": bundle is not None,
                        "snippets": matches,
                    }
                )
                total += 1
            if total >= 40:
                break
        return results

    # ================================================================== cards
    @_bridge
    def get_cards(self, key: str) -> dict[str, Any]:
        directory = self._directory(self._recording(key))
        bundle = self._lesson_bundle(directory)
        if bundle is None:
            raise UserFacingError("Карточки появятся, когда будет готов конспект.")
        cards: list[StudyCard] = bundle["cards"]
        return {
            "cards": [self._card_payload(key, card) for card in cards],
            "stats": self._study.deck_stats(key, [card.card_id for card in cards]).to_dict(),
        }

    @_bridge
    def review_card(self, key: str, card_id: str, remembered: bool) -> dict[str, Any]:
        directory = self._directory(self._recording(key))
        bundle = self._lesson_bundle(directory)
        card_ids = [card.card_id for card in bundle["cards"]] if bundle else []
        if card_id not in card_ids:
            raise UserFacingError("Карточка не найдена — возможно, конспект изменился.")
        state = self._study.record_review(key, card_id, bool(remembered))
        return {
            "box": state.box,
            "due_date": state.due,
            "stats": self._study.deck_stats(key, card_ids).to_dict(),
        }

    @_bridge
    def reset_cards(self, key: str) -> None:
        self._study.reset_deck(str(key))
        self._tasks.notify_external_change()

    @_bridge
    def review_queue(self, include_new: bool = True, limit: int = 60) -> dict[str, Any]:
        queue: list[dict[str, Any]] = []
        lectures: list[dict[str, Any]] = []
        today = _today()
        for recording in self._recordings():
            key = lecture_key(recording)
            try:
                bundle = self._lesson_bundle(self._directory(recording))
            except Exception:
                continue
            if bundle is None or not bundle["cards"]:
                continue
            cards: list[StudyCard] = bundle["cards"]
            stats = self._study.deck_stats(key, [card.card_id for card in cards], today=today)
            lectures.append({"key": key, "title": recording.title, "stats": stats.to_dict()})
            for card in cards:
                state = self._study.card_state(key, card.card_id)
                if not state.is_due(today) or (state.is_new and not include_new):
                    continue
                payload = self._card_payload(key, card)
                payload["lecture_key"] = key
                payload["lecture_title"] = recording.title
                queue.append(payload)
        # Repeat forgotten cards first, then cards in learning, then new ones.
        queue.sort(key=lambda item: (item["is_new"], item["box"]))
        return {"cards": queue[: max(1, int(limit))], "total_due": len(queue), "lectures": lectures}

    # ================================================================ library
    @_bridge
    def rename(self, key: str, title: str) -> dict[str, Any]:
        recording = self._recording(key)
        rename_recording(
            self._library_path,
            recording.meeting_id,
            str(title or ""),
            source_url=recording.source_url,
        )
        self._tasks.notify_external_change()
        return self._lecture_summary(self._recording(key))

    @_bridge
    def trash(self, key: str) -> None:
        recording = self._recording(key)
        if self._tasks.active_for(key) is not None:
            raise UserFacingError("Сначала останови обработку этой лекции.")
        move_to_trash(
            self._library_path,
            recording.meeting_id,
            self._data_dir,
            source_url=recording.source_url,
        )
        self._tasks.notify_external_change()

    @_bridge
    def list_trash(self) -> list[dict[str, Any]]:
        items = []
        for item in list_trash(self._data_dir):
            items.append(
                {
                    "meeting_id": str(item.get("meeting_id", "")),
                    "source_url": str(item.get("source_url", "")),
                    "title": str(item.get("original_title") or item.get("title") or "Без названия"),
                    "size_bytes": int(item.get("total_bytes") or 0),
                    "trashed_label": format_imported_at(str(item.get("trashed_at", ""))),
                }
            )
        return items

    @_bridge
    def restore(self, meeting_id: str, source_url: str = "") -> None:
        restore_from_trash(
            self._library_path,
            str(meeting_id),
            self._data_dir,
            source_url=str(source_url) or None,
        )
        self._tasks.notify_external_change()

    @_bridge
    def empty_trash(self) -> int:
        return empty_trash(self._data_dir)

    @_bridge
    def open_folder(self, key: str) -> None:
        directory = self._directory(self._recording(key))
        directory.mkdir(parents=True, exist_ok=True)
        self._reveal(directory)

    @_bridge
    def open_data_folder(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._reveal(self._data_dir)

    @_bridge
    def export_lesson(self, key: str, fmt: str) -> dict[str, Any] | None:
        from ..lesson_export import export_lesson_to_html_file, export_lesson_to_pdf_file

        recording = self._recording(key)
        directory = self._directory(recording)
        base_name = _safe_file_name(recording.title)
        if fmt == "zip":
            target = self._file_dialog(
                "save", save_filename=f"{base_name}.zip", file_types=("ZIP-архив (*.zip)",)
            )
            if not target:
                return None
            path = export_lecture_archive(directory, _with_suffix(Path(target), ".zip"))
            return {"path": str(path)}
        text = self._lesson_text(directory)
        if text is None:
            raise UserFacingError("Сначала создай конспект.")
        if fmt == "pdf":
            target = self._file_dialog(
                "save", save_filename=f"{base_name}.pdf", file_types=("PDF (*.pdf)",)
            )
            if not target:
                return None
            path = export_lesson_to_pdf_file(
                recording.title, text, _with_suffix(Path(target), ".pdf")
            )
            return {"path": str(path)}
        if fmt == "md":
            target = self._file_dialog(
                "save", save_filename=f"{base_name}.md", file_types=("Markdown (*.md)",)
            )
            if not target:
                return None
            from ..atomic_io import atomic_write_text

            path = _with_suffix(Path(target), ".md")
            atomic_write_text(path, text + "\n", encoding="utf-8")
            return {"path": str(path)}
        target = self._file_dialog(
            "save", save_filename=f"{base_name}.html", file_types=("Веб-страница (*.html)",)
        )
        if not target:
            return None
        path = export_lesson_to_html_file(
            recording.title, text, _with_suffix(Path(target), ".html")
        )
        return {"path": str(path)}

    @_bridge
    def reveal_path(self, path: str) -> None:
        target = Path(str(path or ""))
        if not target.exists():
            raise UserFacingError("Файл больше не существует.")
        self._reveal(target)

    # =============================================================== settings
    @_bridge
    def get_settings(self) -> dict[str, Any]:
        return _settings_payload(self._settings())

    @_bridge
    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise UserFacingError("Некорректные настройки.")
        current = self._settings()
        provider = str(values.get("api_provider", current.api_provider))
        if provider not in SUPPORTED_PROVIDERS:
            provider = current.api_provider
        api_key = current.api_key
        if values.get("api_key_changed"):
            api_key = str(values.get("api_key", "")).strip()
        try:
            interval = int(values.get("frame_interval_seconds", current.frame_interval_seconds))
        except (TypeError, ValueError):
            interval = current.frame_interval_seconds
        updated = AppSettings(
            api_provider=provider,
            api_model=str(values.get("api_model", current.api_model)).strip()
            or default_model_for_provider(provider),
            api_key=api_key,
            chatgpt_model=str(values.get("chatgpt_model", current.chatgpt_model)).strip()
            or current.chatgpt_model,
            whisper_model=str(values.get("whisper_model", current.whisper_model)),
            frame_interval_seconds=interval,
            ocr_enabled=bool(values.get("ocr_enabled", current.ocr_enabled)),
        )
        save_settings(updated, self._settings_path)
        with self._lock:
            self._settings_cache = None
        return _settings_payload(self._settings())

    @_bridge
    def chatgpt_status(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            cached = self._chatgpt_cache
        if cached is not None and not force and time.monotonic() - cached[0] < _CHATGPT_STATUS_TTL:
            return cached[1]
        from ..platform_services import PlatformDependencyLocator

        payload: dict[str, Any] = {
            "available": bool(PlatformDependencyLocator().find_codex()),
            "signed_in": False,
            "email": None,
            "plan": None,
            "error": "",
        }
        if payload["available"]:
            from ..chatgpt_account import chatgpt_account_status

            try:
                status = chatgpt_account_status()
                payload.update(
                    signed_in=status.signed_in, email=status.email, plan=status.plan_type
                )
            except Exception as exc:
                payload["error"] = self._error_message("web.chatgpt_status", exc)
        with self._lock:
            self._chatgpt_cache = (time.monotonic(), payload)
        return payload

    @_bridge
    def chatgpt_models(self) -> list[dict[str, str]]:
        from ..chatgpt_account import list_chatgpt_models

        return [
            {"slug": model.slug, "name": model.display_name or model.slug}
            for model in list_chatgpt_models()
        ]

    @_bridge
    def chatgpt_login(self) -> dict[str, Any]:
        from ..chatgpt_account import login_with_chatgpt

        status = login_with_chatgpt()
        payload = {
            "available": True,
            "signed_in": status.signed_in,
            "email": status.email,
            "plan": status.plan_type,
            "error": "",
        }
        with self._lock:
            self._chatgpt_cache = (time.monotonic(), payload)
        return payload

    @_bridge
    def system_check(self) -> dict[str, Any]:
        from ..diagnostics import collect_system_diagnostics, diagnostic_log_path

        report = collect_system_diagnostics()
        dependencies = report.get("dependencies", {})
        languages = dependencies.get("tesseract_languages") or []
        return {
            "ffmpeg": bool(dependencies.get("ffmpeg_available")),
            "tesseract": bool(dependencies.get("tesseract_available")),
            "tesseract_rus": "rus" in languages,
            "codex": bool(dependencies.get("codex_available")),
            "storage_ok": report.get("status") == "ok",
            "data_dir": str(self._data_dir),
            "log_path": str(diagnostic_log_path()),
            "python": report.get("platform", {}).get("python_version", ""),
        }

    @_bridge
    def open_external(self, url: str) -> None:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise UserFacingError("Можно открывать только веб-ссылки.")
        self._open_url(parsed.geturl())


# ============================================================== helpers
def lecture_key(recording: BBBRecording) -> str:
    """A short, URL-safe identifier of a lecture for the UI and media URLs."""

    digest = hashlib.sha1(f"{recording.source_url}\n{recording.meeting_id}".encode("utf-8"))
    return digest.hexdigest()[:16]


def _follow_up(then_generate: Any) -> dict[str, Any]:
    """Task params that make a finished preparation start generation automatically."""

    if not isinstance(then_generate, dict) or then_generate.get("provider") not in {
        "chatgpt",
        "api",
    }:
        return {}
    return {
        "then_generate": {
            "provider": then_generate["provider"],
            "model": str(then_generate.get("model", "")),
        }
    }


def _settings_payload(settings: AppSettings) -> dict[str, Any]:
    return {
        "api_provider": settings.api_provider,
        "api_model": settings.api_model,
        "api_key_set": bool(settings.api_key.strip()),
        "api_key_placeholder": _PLACEHOLDER_KEY if settings.api_key.strip() else "",
        "api_configured": settings.api_configured,
        "provider_label": settings.provider_label,
        "chatgpt_model": settings.chatgpt_model,
        "whisper_model": settings.whisper_model,
        "frame_interval_seconds": settings.frame_interval_seconds,
        "ocr_enabled": settings.ocr_enabled,
        "options": {
            "providers": [
                {"id": provider, "label": "OpenAI" if provider == "openai" else "DeepSeek"}
                for provider in SUPPORTED_PROVIDERS
            ],
            "default_models": {
                provider: default_model_for_provider(provider) for provider in SUPPORTED_PROVIDERS
            },
            "whisper_models": list(SUPPORTED_WHISPER_MODELS),
            "frame_intervals": list(SUPPORTED_FRAME_INTERVALS),
        },
    }


def _today() -> date:
    return date.today()


def _frame_files(directory: Path) -> list[Path]:
    frames_dir = directory / "frames"
    if not frames_dir.is_dir():
        return []
    return sorted(path for path in frames_dir.glob("frame-*.jpg") if path.is_file())


def _frame_interval(directory: Path) -> int:
    try:
        value = int((directory / "frames" / "interval-seconds.txt").read_text("ascii").strip())
    except (OSError, ValueError):
        return 60
    return value if value > 0 else 60


def _read_transcript_segments(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("segments", [])
    if not isinstance(payload, list):
        return []
    segments = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start_seconds", item.get("start", 0)))
            end = float(item.get("end_seconds", item.get("end", start)))
        except (TypeError, ValueError):
            continue
        text = str(item.get("text", "")).strip()
        if text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def _snippet(text: str, position: int, length: int, radius: int = 70) -> str:
    start = max(0, position - radius)
    end = min(len(text), position + length + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _safe_file_name(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "Конспект")[:80]


def _with_suffix(path: Path, suffix: str) -> Path:
    return path if path.suffix.lower() == suffix else path.with_name(path.name + suffix)


def _reveal_in_file_manager(path: Path) -> None:
    """Show a folder, or select a file in its folder, in the system file manager."""

    resolved = path.resolve()
    if sys.platform == "win32":
        if resolved.is_file():
            # Explorer parses its own command line; the path must be quoted after the comma.
            subprocess.Popen(f'explorer.exe /select,"{resolved}"')
            return
        from ..platform_services import PlatformSystemActions

        PlatformSystemActions().open_in_file_manager(resolved)
        return
    if sys.platform == "darwin":
        args = ["open", "-R", str(resolved)] if resolved.is_file() else ["open", str(resolved)]
        subprocess.Popen(args)
        return
    target = resolved.parent if resolved.is_file() else resolved
    subprocess.Popen(["xdg-open", str(target)])
