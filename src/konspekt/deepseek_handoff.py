"""Prepare a user-controlled handoff from local lecture materials to DeepSeek Web."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .atomic_io import AtomicIOError, atomic_write_text
from .bbb_import import BBBRecording
from .local_pipeline import default_lecture_directory
from .outbound_context import (
    OutboundContextError,
    _validate_outbound_text,
    validate_provider_context_limits,
)
from .platform_services import PlatformKeyboardConventions, PlatformSystemActions

DEEPSEEK_URL = "https://chat.deepseek.com/"


class DeepSeekHandoffError(RuntimeError):
    """The local package is not ready for a DeepSeek Web handoff."""


@dataclass(frozen=True)
class DeepSeekHandoff:
    directory: Path
    context_path: Path
    prompt_path: Path
    instructions_path: Path


def prepare_deepseek_handoff(
    recording: BBBRecording,
    *,
    directory: Path | None = None,
) -> DeepSeekHandoff:
    """Create a local checklist for the DeepSeek Web flow without an API call."""

    target = directory or default_lecture_directory(recording)
    context_path = target / "lesson-context.md"
    prompt_path = target / "lesson-prompt.md"
    missing = [path.name for path in (context_path, prompt_path) if not path.is_file()]
    if missing:
        raise DeepSeekHandoffError(
            "Сначала собери пакет контекста: не найдены " + ", ".join(missing) + "."
        )

    try:
        context_text = context_path.read_text(encoding="utf-8")
        prompt_text = prompt_path.read_text(encoding="utf-8")
        forbidden = tuple(f for f in (recording.meeting_id, recording.source_url) if f)
        _validate_outbound_text("deepseek_context", context_text, forbidden)
        _validate_outbound_text("deepseek_prompt", prompt_text, forbidden)
        total_chars = len(prompt_text) + len(context_text)
        total_bytes = len(prompt_text.encode("utf-8")) + len(context_text.encode("utf-8"))
        validate_provider_context_limits("deepseek_web", total_chars, total_bytes)
    except OutboundContextError as exc:
        raise DeepSeekHandoffError(f"Ошибка проверки безопасности файлов передачи: {exc}") from exc
    except OSError as exc:
        raise DeepSeekHandoffError("Не удалось прочитать пакет контекста.") from exc

    instructions_text = _render_handoff_instructions(recording.title)
    instructions_path = target / "deepseek-handoff.md"
    try:
        atomic_write_text(instructions_path, instructions_text, encoding="utf-8")
    except (AtomicIOError, OSError) as exc:
        raise DeepSeekHandoffError(f"Не удалось записать файл инструкций handoff: {exc}") from exc

    return DeepSeekHandoff(
        directory=target,
        context_path=context_path,
        prompt_path=prompt_path,
        instructions_path=instructions_path,
    )


def launch_deepseek_handoff(
    handoff: DeepSeekHandoff,
    *,
    open_url: Callable[[str], bool] = webbrowser.open_new_tab,
    open_directory: Callable[[Path], None] | None = None,
    recording: BBBRecording | None = None,
) -> None:
    """Open DeepSeek and the local context folder; the user chooses the chat and sends."""

    if not handoff.context_path.is_file() or not handoff.prompt_path.is_file():
        raise DeepSeekHandoffError("Пакет контекста больше недоступен в локальной папке.")

    try:
        context_text = handoff.context_path.read_text(encoding="utf-8")
        prompt_text = handoff.prompt_path.read_text(encoding="utf-8")
        forbidden = ()
        if recording is not None:
            forbidden = tuple(f for f in (recording.meeting_id, recording.source_url) if f)
        _validate_outbound_text("deepseek_context", context_text, forbidden)
        _validate_outbound_text("deepseek_prompt", prompt_text, forbidden)
        total_chars = len(prompt_text) + len(context_text)
        total_bytes = len(prompt_text.encode("utf-8")) + len(context_text.encode("utf-8"))
        validate_provider_context_limits("deepseek_web", total_chars, total_bytes)
    except OutboundContextError as exc:
        raise DeepSeekHandoffError(f"Ошибка проверки безопасности файлов передачи: {exc}") from exc
    except OSError as exc:
        raise DeepSeekHandoffError("Не удалось прочитать пакет контекста перед отправкой.") from exc

    try:
        opened = open_url(DEEPSEEK_URL)
        if not opened:
            raise RuntimeError("browser did not accept the address")
        (open_directory or _open_in_file_manager)(handoff.directory)
    except OSError as exc:
        raise DeepSeekHandoffError("Не удалось открыть DeepSeek или папку с материалами.") from exc
    except RuntimeError as exc:
        raise DeepSeekHandoffError("Не удалось открыть DeepSeek в браузере по умолчанию.") from exc


def _open_in_file_manager(directory: Path) -> None:
    PlatformSystemActions().open_in_file_manager(directory)


def _render_handoff_instructions(title: str) -> str:
    shortcut = PlatformKeyboardConventions().format_shortcut("V")
    return f"""# Передача лекции в DeepSeek Web

Лекция: **{title}**

Этот этап не использует API. Приложение открывает chat.deepseek.com, папку с материалами и копирует инструкцию в буфер обмена. Самостоятельно выбрать чат, прикрепить файл и отправить сообщение должен пользователь.

1. В DeepSeek выбери новый или подходящий существующий чат.
2. Прикрепи файл lesson-context.md из этой папки.
3. Вставь подготовленную инструкцию сочетанием {shortcut}.
4. Не включай веб-поиск: итоговый конспект должен опираться на приложенный контекст лекции.
5. Проверь, что прикреплён именно файл контекста этой лекции, и отправь сообщение.
6. Сохрани ответ DeepSeek как lesson.md в этой же папке.

Если интерфейс не принимает файл, открой lesson-context.md в текстовом редакторе, вставь его содержимое в чат и затем добавь инструкцию из lesson-prompt.md.
"""
