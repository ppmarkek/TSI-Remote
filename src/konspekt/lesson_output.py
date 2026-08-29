"""Store and read the final Markdown lesson returned by a web chat."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .atomic_io import AtomicIOError, atomic_write_text
from .bbb_import import BBBRecording
from .local_pipeline import default_lecture_directory


class LessonOutputError(RuntimeError):
    """The generated Markdown cannot be stored or read yet."""


@dataclass(frozen=True)
class SavedLesson:
    directory: Path
    path: Path
    character_count: int


def lesson_path(recording: BBBRecording, *, directory: Path | None = None) -> Path:
    target = directory or default_lecture_directory(recording)
    return target / "lesson.md"


def lesson_is_ready(recording: BBBRecording) -> bool:
    path = lesson_path(recording)
    return path.is_file() and path.stat().st_size > 0


def save_generated_lesson(
    recording: BBBRecording,
    markdown: str,
    *,
    directory: Path | None = None,
) -> SavedLesson:
    """Save the user-reviewed chat answer as the lecture's local Markdown file."""

    cleaned = markdown.replace("\r\n", "\n").strip()
    if not cleaned:
        raise LessonOutputError("Вставь полный ответ из чата перед сохранением.")

    path = lesson_path(recording, directory=directory)
    try:
        atomic_write_text(path, cleaned + "\n", encoding="utf-8")
    except (AtomicIOError, OSError) as exc:
        raise LessonOutputError(f"Не удалось сохранить файл lesson.md: {exc}") from exc

    return SavedLesson(
        directory=path.parent,
        path=path,
        character_count=len(cleaned),
    )


def read_generated_lesson(
    recording: BBBRecording,
    *,
    directory: Path | None = None,
) -> str:
    path = lesson_path(recording, directory=directory)
    if not path.is_file():
        raise LessonOutputError("Файл lesson.md пока не сохранён.")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LessonOutputError("Не удалось прочитать файл lesson.md.") from exc
    if not content:
        raise LessonOutputError("Файл lesson.md пустой.")
    return content


def save_lesson_markdown(directory: Path, markdown: str) -> Path:
    """Save markdown text directly to lesson.md in the target directory."""
    cleaned = markdown.replace("\r\n", "\n").strip()
    if not cleaned:
        raise LessonOutputError("Конспект не может быть пустым.")
    target = directory / "lesson.md"
    try:
        atomic_write_text(target, cleaned + "\n", encoding="utf-8")
    except (AtomicIOError, OSError) as exc:
        raise LessonOutputError(f"Не удалось сохранить файл lesson.md: {exc}") from exc
    return target
