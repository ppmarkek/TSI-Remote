"""Pure workflow state machine and next action resolution for lecture processing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .lecture_manifest import LectureManifest


class LectureState(str, Enum):
    IMPORTED = "imported"
    DOWNLOADING = "downloading"
    PREPARED = "prepared"
    PACKAGE_READY = "package_ready"
    AWAITING_CONSENT = "awaiting_consent"
    GENERATING = "generating"
    LESSON_READY = "lesson_ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERABLE_PARTIAL = "recoverable_partial"


@dataclass(frozen=True)
class WorkflowCapabilities:
    api_configured: bool = False
    chatgpt_signed_in: bool = False
    whisper_available: bool = True
    tesseract_available: bool = True


@dataclass(frozen=True)
class WorkflowAction:
    action_type: str
    label: str
    description: str
    enabled: bool = True


def resolve_lecture_state(
    directory: Path,
    manifest: LectureManifest | None = None,
) -> LectureState:
    """Determine the current lifecycle state of a lecture from disk artifacts and manifest."""
    lesson_path = directory / "lesson.md"
    if lesson_path.is_file():
        try:
            if lesson_path.stat().st_size > 0:
                return LectureState.LESSON_READY
        except OSError:
            pass

    context_path = directory / "lesson-context.md"
    prompt_path = directory / "lesson-prompt.md"
    if context_path.is_file() and prompt_path.is_file():
        return LectureState.PACKAGE_READY

    transcript_path = directory / "transcript.json"
    audio_path = directory / "audio.mp4"
    audio_part = directory / "audio.mp4.part"
    screen_part = directory / "screen.mp4.part"

    if audio_part.is_file() or screen_part.is_file():
        return LectureState.RECOVERABLE_PARTIAL

    if transcript_path.is_file() and transcript_path.stat().st_size > 2:
        return LectureState.PREPARED

    if audio_path.is_file() and audio_path.stat().st_size > 0:
        return LectureState.RECOVERABLE_PARTIAL

    if manifest is not None:
        download_stage = manifest.stages.get("download")
        if download_stage and download_stage.status == "failed":
            return LectureState.FAILED

    return LectureState.IMPORTED


def next_action(
    state: LectureState,
    capabilities: WorkflowCapabilities = WorkflowCapabilities(),
) -> WorkflowAction:
    """Return the next recommended action for the user based on state and system capabilities."""
    if state == LectureState.LESSON_READY:
        return WorkflowAction(
            action_type="view_lesson",
            label="Открыть конспект",
            description="Готовый учебный конспект сохранён локально.",
            enabled=True,
        )

    if state == LectureState.PACKAGE_READY:
        return WorkflowAction(
            action_type="request_consent",
            label="Создать конспект",
            description="Пакет контекста собран. Выбери способ создания конспекта.",
            enabled=True,
        )

    if state == LectureState.PREPARED:
        return WorkflowAction(
            action_type="build_package",
            label="Собрать пакет контекста",
            description="Транскрипция и текст слайдов готовы к объединению.",
            enabled=True,
        )

    if state in (LectureState.RECOVERABLE_PARTIAL, LectureState.CANCELLED):
        return WorkflowAction(
            action_type="resume",
            label="Возобновить подготовку",
            description="Продолжить локальную подготовку с сохранённых файлов.",
            enabled=True,
        )

    if state == LectureState.FAILED:
        return WorkflowAction(
            action_type="retry",
            label="Повторить попытку",
            description="Повторить обработку лекции после ошибки.",
            enabled=True,
        )

    if state == LectureState.DOWNLOADING:
        return WorkflowAction(
            action_type="cancel",
            label="Отменить скачивание",
            description="Скачивание дорожек лекции выполняется в фоне.",
            enabled=True,
        )

    if state == LectureState.GENERATING:
        return WorkflowAction(
            action_type="cancel",
            label="Отменить создание",
            description="Конспект формируется моделью.",
            enabled=True,
        )

    # Default for IMPORTED
    return WorkflowAction(
        action_type="prepare_local",
        label="Подготовить лекцию",
        description="Скачать медиа, распознать речь и текст слайдов локально.",
        enabled=True,
    )
