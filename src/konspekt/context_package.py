"""Build a compact local context package from prepared lecture materials."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_json, atomic_write_text
from .bbb_import import BBBRecording, SlideInfo
from .job_runner import CancellationToken
from .local_pipeline import ScreenNote, TranscriptSegment, default_lecture_directory
from .outbound_context import (
    OutboundContext,
    OutboundContextError,
    build_outbound_context,
)


class ContextPackageError(RuntimeError):
    """The prepared lecture files cannot be turned into a chat context yet."""


ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class TimelineBlock:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class ContextPackage:
    directory: Path
    markdown_path: Path
    json_path: Path
    prompt_path: Path
    timeline_block_count: int
    slide_count: int
    screen_note_count: int
    outbound_context: OutboundContext | None = None


def context_package_is_ready(
    recording: BBBRecording,
    base_dir: Path | None = None,
) -> bool:
    """Return whether the lecture already has the two files needed for a chat."""

    target = default_lecture_directory(recording, base_dir=base_dir)
    return (target / "lesson-context.md").is_file() and (target / "lesson-prompt.md").is_file()


def build_context_package(
    recording: BBBRecording,
    *,
    directory: Path | None = None,
    progress: ProgressCallback | None = None,
    max_block_seconds: int = 150,
    max_block_characters: int = 2600,
    cancellation_token: CancellationToken | None = None,
) -> ContextPackage:
    """Create compact, attachable context without invoking an LLM or external API."""

    if max_block_seconds <= 0 or max_block_characters <= 0:
        raise ValueError("Context block limits must be positive")

    notify = progress or _do_nothing
    if cancellation_token is not None:
        cancellation_token.check_cancelled()
    target = directory or default_lecture_directory(recording)
    transcript_path = target / "transcript.json"
    if not transcript_path.is_file():
        raise ContextPackageError(
            "Сначала подготовь материалы лекции: не найден файл транскрипции."
        )

    notify(10, "Проверяем подготовленные материалы…")
    notify(25, "Собираем транскрипцию по временным блокам…")
    if cancellation_token is not None:
        cancellation_token.check_cancelled()
    segments = _read_transcript(transcript_path)
    blocks = _group_transcript(
        segments,
        max_block_seconds=max_block_seconds,
        max_block_characters=max_block_characters,
    )

    notify(55, "Объединяем текст слайдов и заметки с экрана…")
    if cancellation_token is not None:
        cancellation_token.check_cancelled()
    slides = _unique_slides(recording.slides)
    screen_notes = _read_screen_notes(target / "screen-notes.json")

    try:
        outbound = build_outbound_context(
            recording.title,
            slides=slides,
            screen_notes=screen_notes,
            transcript_blocks=blocks,
            meeting_id=recording.meeting_id,
            source_url=recording.source_url,
        )
    except OutboundContextError as exc:
        raise ContextPackageError(f"Ошибка формирования безопасного контекста: {exc}") from exc

    json_path = target / "lesson-context.json"
    markdown_path = target / "lesson-context.md"
    prompt_path = target / "lesson-prompt.md"
    notify(78, "Создаём Markdown и структурированные данные…")
    if cancellation_token is not None:
        cancellation_token.check_cancelled()

    try:
        atomic_write_json(
            json_path,
            outbound.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        atomic_write_text(
            markdown_path,
            outbound.render_markdown(),
            encoding="utf-8",
        )
        atomic_write_text(
            prompt_path,
            outbound.render_prompt(),
            encoding="utf-8",
        )
    except (AtomicIOError, OSError) as exc:
        raise ContextPackageError(f"Не удалось сохранить пакет контекста: {exc}") from exc

    notify(100, "Пакет контекста готов для прикрепления в чат.")

    return ContextPackage(
        directory=target,
        markdown_path=markdown_path,
        json_path=json_path,
        prompt_path=prompt_path,
        timeline_block_count=len(blocks),
        slide_count=len(slides),
        screen_note_count=len(screen_notes),
        outbound_context=outbound,
    )


def _read_transcript(path: Path) -> tuple[TranscriptSegment, ...]:
    payload = _read_json(path, "транскрипции")
    if not isinstance(payload, list):
        raise ContextPackageError("Файл транскрипции имеет неверный формат.")

    segments: list[TranscriptSegment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        text = _normalise_text(str(item.get("text", "")))
        if text:
            segments.append(TranscriptSegment(start, max(start, end), text))
    return tuple(sorted(segments, key=lambda segment: segment.start_seconds))


def _read_screen_notes(path: Path) -> tuple[ScreenNote, ...]:
    if not path.is_file():
        return ()
    payload = _read_json(path, "заметок с экрана")
    if not isinstance(payload, list):
        return ()

    notes: list[ScreenNote] = []
    seen_text: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = _normalise_text(str(item.get("text", "")))
        fingerprint = text.casefold()
        if not text or fingerprint in seen_text:
            continue
        try:
            timestamp = float(item["timestamp_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        seen_text.add(fingerprint)
        notes.append(
            ScreenNote(
                timestamp_seconds=timestamp,
                image_path=str(item.get("image_path", "")),
                text=text,
            )
        )
    return tuple(sorted(notes, key=lambda note: note.timestamp_seconds))


def _read_json(path: Path, title: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextPackageError(f"Не удалось прочитать файл {title}.") from exc


def _unique_slides(slides: tuple[SlideInfo, ...]) -> tuple[SlideInfo, ...]:
    unique: list[SlideInfo] = []
    seen_text: set[str] = set()
    for index, slide in enumerate(slides, start=1):
        text = _normalise_text(slide.text)
        fingerprint = text.casefold()
        if fingerprint and fingerprint in seen_text:
            continue
        if fingerprint:
            seen_text.add(fingerprint)
        unique.append(
            SlideInfo(
                identifier=slide.identifier or f"slide-{index}",
                text=text,
                image_url=slide.image_url,
            )
        )
    return tuple(unique)


def _group_transcript(
    segments: tuple[TranscriptSegment, ...],
    *,
    max_block_seconds: int,
    max_block_characters: int,
) -> tuple[TimelineBlock, ...]:
    blocks: list[TimelineBlock] = []
    start: float | None = None
    end = 0.0
    parts: list[str] = []

    def flush() -> None:
        nonlocal start, end, parts
        if start is not None and parts:
            blocks.append(TimelineBlock(start, end, " ".join(parts)))
        start = None
        end = 0.0
        parts = []

    for segment in segments:
        next_text = segment.text
        if start is not None:
            is_too_long = segment.end_seconds - start > max_block_seconds
            is_too_wide = len(" ".join(parts)) + len(next_text) + 1 > max_block_characters
            if is_too_long or is_too_wide:
                flush()
        if start is None:
            start = segment.start_seconds
        end = max(end, segment.end_seconds)
        parts.append(next_text)
    flush()
    return tuple(blocks)


def _normalise_text(value: str) -> str:
    return " ".join(value.split())


def _do_nothing(_: int, __: str) -> None:
    pass
