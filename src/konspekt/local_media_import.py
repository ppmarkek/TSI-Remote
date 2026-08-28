"""Import local audio and video files directly into the study library."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from .bbb_import import BBBRecording, save_to_library
from .lecture_manifest import LectureManifest, file_sha256
from .local_pipeline import default_lecture_directory

SUPPORTED_MEDIA_EXTENSIONS = {".mp4", ".mp3", ".m4a", ".wav", ".mkv", ".webm", ".aac"}


class LocalMediaImportError(RuntimeError):
    """A local media file could not be imported into the library."""


def import_local_media_file(
    media_path: Path,
    *,
    custom_title: str | None = None,
    library_path: Path | None = None,
    base_dir: Path | None = None,
) -> tuple[BBBRecording, Path]:
    """Import a local audio/video file into the library with content-hash identity."""
    if not media_path.is_file():
        raise LocalMediaImportError(f"Файл не найден: {media_path}")

    ext = media_path.suffix.lower()
    if ext not in SUPPORTED_MEDIA_EXTENSIONS:
        raise LocalMediaImportError(
            f"Неподдерживаемый формат файла: {ext}. Поддерживаются: {', '.join(sorted(SUPPORTED_MEDIA_EXTENSIONS))}"
        )

    # Compute content hash for stable ID
    content_hash = file_sha256(media_path)[:16]
    meeting_id = f"local-{content_hash}"
    title = custom_title.strip() if custom_title and custom_title.strip() else media_path.stem

    recording = BBBRecording(
        meeting_id=meeting_id,
        source_url=f"local://{media_path.name}",
        title=title,
        imported_at=datetime.now(timezone.utc).isoformat(),
        audio_video_url=str(media_path.resolve()),
        screen_video_url=None,
        slides=(),
    )

    resolved_base_dir = base_dir or (library_path.parent if library_path else None)
    lec_dir = default_lecture_directory(recording, base_dir=resolved_base_dir)
    lec_dir.mkdir(parents=True, exist_ok=True)

    # Copy audio track to destination
    dest_audio = lec_dir / "audio.mp4"
    if not dest_audio.is_file():
        try:
            shutil.copy2(media_path, dest_audio)
        except OSError as exc:
            raise LocalMediaImportError(f"Не удалось скопировать медиафайл: {exc}") from exc

    # Record manifest
    manifest = LectureManifest.for_recording(title, meeting_id, recording.source_url, lec_dir)
    manifest.record_stage_success(
        "download",
        fingerprint={"local_file": media_path.name, "source_hash": content_hash},
        outputs={"audio.mp4": file_sha256(dest_audio)},
    )
    manifest.save(lec_dir / "lecture-manifest.json")

    # Persist in library
    save_to_library(recording, path=library_path)

    return recording, lec_dir
