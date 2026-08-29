"""Import local audio and video files directly into the study library."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .bbb_import import BBBRecording
from .lecture_manifest import LectureManifest, ManifestError, file_sha256
from .library_manager import save_to_library
from .local_pipeline import default_lecture_directory

SUPPORTED_MEDIA_EXTENSIONS = {".mp4", ".mp3", ".m4a", ".wav", ".mkv", ".webm", ".aac"}
_COPY_CHUNK_SIZE = 1024 * 1024


class LocalMediaImportError(RuntimeError):
    """A local media file could not be imported into the library."""


def import_local_media_file(
    media_path: Path,
    *,
    custom_title: str | None = None,
    library_path: Path | None = None,
    base_dir: Path | None = None,
) -> tuple[BBBRecording, Path]:
    """Import a local audio/video file with content-hash identity.

    The destination is never written in place.  Bytes are copied to a sibling
    ``.part`` file, flushed, hashed, and atomically replaced only after the
    digest matches the source.  A failed retry therefore cannot turn a partial
    file into a successful manifest entry.
    """

    source = media_path.expanduser()
    if not source.is_file():
        raise LocalMediaImportError(f"Файл не найден: {source}")

    ext = source.suffix.lower()
    if ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise LocalMediaImportError(
            f"Неподдерживаемый формат файла: {ext}. "
            f"Поддерживаются: {', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}"
        )

    source_hash = file_sha256(source)
    if not source_hash:
        raise LocalMediaImportError("Не удалось вычислить хеш выбранного медиафайла.")
    try:
        source_size = source.stat().st_size
    except OSError as exc:
        raise LocalMediaImportError(f"Не удалось прочитать медиафайл: {exc}") from exc
    if source_size <= 0:
        raise LocalMediaImportError("Выбранный медиафайл пуст.")

    content_id = source_hash[:16]
    meeting_id = f"local-{content_id}"
    source_url = f"local://media-{content_id}"
    title = custom_title.strip() if custom_title and custom_title.strip() else source.stem

    recording = BBBRecording(
        meeting_id=meeting_id,
        source_url=source_url,
        title=title,
        imported_at=datetime.now(timezone.utc).isoformat(),
        # Keep the stored reference relative to the lecture directory so no
        # absolute user path enters library.json or outbound context.
        audio_video_url="local://audio.mp4",
        screen_video_url=None,
        slides=(),
    )

    resolved_library_path = library_path or (base_dir / "library.json" if base_dir else None)
    resolved_base_dir = base_dir or (
        resolved_library_path.parent if resolved_library_path else None
    )
    lecture_dir = default_lecture_directory(recording, base_dir=resolved_base_dir)
    lecture_dir.mkdir(parents=True, exist_ok=True)

    destination = lecture_dir / "audio.mp4"
    _copy_verified_atomically(source, destination, source_hash)

    manifest = LectureManifest.for_recording(title, meeting_id, recording.source_url, lecture_dir)
    manifest.record_stage_success(
        "download",
        fingerprint={
            "source_sha256": source_hash,
            "source_size": source_size,
        },
        outputs={"audio.mp4": source_hash},
    )
    try:
        manifest.save(lecture_dir / "lecture-manifest.json")
    except ManifestError as exc:
        raise LocalMediaImportError(
            f"Не удалось сохранить манифест локальной лекции: {exc}"
        ) from exc

    save_to_library(recording, path=resolved_library_path)
    return recording, lecture_dir


def _copy_verified_atomically(source: Path, destination: Path, source_hash: str) -> None:
    if destination.is_file() and not destination.is_symlink():
        if file_sha256(destination) == source_hash:
            return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.part")
    try:
        temporary.unlink(missing_ok=True)
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=_COPY_CHUNK_SIZE)
            output_stream.flush()
            try:
                os.fsync(output_stream.fileno())
            except OSError:
                pass

        if file_sha256(temporary) != source_hash:
            raise LocalMediaImportError(
                "Проверка скопированного медиафайла не прошла: хеши не совпадают."
            )
        os.replace(temporary, destination)
    except LocalMediaImportError:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LocalMediaImportError(f"Не удалось атомарно скопировать медиафайл: {exc}") from exc
