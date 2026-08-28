"""Library operations: searching, filtering, renaming, exporting, and safe trashing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_json
from .bbb_import import BBBRecording, load_library, save_library
from .local_pipeline import default_lecture_directory
from .workflow import LectureState, resolve_lecture_state


@dataclass(frozen=True)
class TrashedItem:
    meeting_id: str
    original_title: str
    trashed_at: str
    total_bytes: int
    source_url: str


def filter_and_sort_recordings(
    recordings: list[BBBRecording],
    *,
    query: str = "",
    state_filter: LectureState | None = None,
    sort_by: str = "date_desc",
    base_dir: Path | None = None,
) -> list[BBBRecording]:
    """Filter and sort recording snapshots in-memory without repetitive filesystem queries."""
    result = list(recordings)

    if query.strip():
        q = query.strip().lower()
        result = [r for r in result if q in r.title.lower()]

    if state_filter is not None and base_dir is not None:
        result = [
            r
            for r in result
            if resolve_lecture_state(default_lecture_directory(r, base_dir=base_dir))
            == state_filter
        ]

    if sort_by == "title_asc":
        result.sort(key=lambda r: r.title.lower())
    elif sort_by == "title_desc":
        result.sort(key=lambda r: r.title.lower(), reverse=True)
    elif sort_by == "date_asc":
        result.sort(key=lambda r: r.imported_at or "")
    else:  # default date_desc
        result.sort(key=lambda r: r.imported_at or "", reverse=True)

    return result


def rename_recording(
    library_path: Path,
    meeting_id: str,
    new_title: str,
    source_url: str | None = None,
) -> list[BBBRecording]:
    """Rename a lecture in library.json safely while keeping original identifiers intact."""
    clean_title = new_title.strip()
    if not clean_title:
        raise ValueError("Название лекции не может быть пустым.")

    recordings = load_library(library_path)
    updated: list[BBBRecording] = []
    found = False
    from .bbb_import import _source_origin

    target_origin = _source_origin(source_url) if source_url else None
    for r in recordings:
        matches = r.meeting_id == meeting_id and (
            target_origin is None or _source_origin(r.source_url) == target_origin
        )
        if matches:
            updated.append(
                BBBRecording(
                    meeting_id=r.meeting_id,
                    source_url=r.source_url,
                    title=clean_title,
                    imported_at=r.imported_at,
                    audio_video_url=r.audio_video_url,
                    screen_video_url=r.screen_video_url,
                    slides=r.slides,
                )
            )
            found = True
        else:
            updated.append(r)

    if not found:
        raise ValueError(f"Лекция {meeting_id} не найдена в библиотеке.")

    save_library(updated, path=library_path)
    return updated


def calculate_library_size(
    recordings: list[BBBRecording],
    base_dir: Path,
) -> int:
    """Return the size of active lecture material without following symlinks."""
    total = 0
    seen: set[Path] = set()
    for recording in recordings:
        directory = default_lecture_directory(recording, base_dir=base_dir)
        if not directory.is_dir():
            legacy = base_dir / recording.meeting_id
            directory = legacy if legacy.is_dir() else directory
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def export_lecture_archive(
    lecture_dir: Path,
    export_zip_path: Path,
) -> Path:
    """Create a self-contained clean zip archive containing only public notes and materials."""
    if not lecture_dir.is_dir():
        raise ValueError("Каталог лекции не существует.")

    export_zip_path.parent.mkdir(parents=True, exist_ok=True)
    temp_zip = export_zip_path.with_name(f"{export_zip_path.name}.tmp")
    temp_zip.unlink(missing_ok=True)

    allowed_names = {"lesson.md", "lesson-context.md", "transcript.json"}
    allowed_dirs = {"slides", "ocr_frames"}

    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(lecture_dir):
            root_path = Path(root)
            rel_root = root_path.relative_to(lecture_dir)

            for file_name in files:
                file_path = root_path / file_name
                rel_file = rel_root / file_name

                # Filter out manifests with private metadata or internal temporary files
                if file_name.endswith(".part") or file_name.endswith(".tmp"):
                    continue
                if file_name == "lecture-manifest.json":
                    continue

                if rel_root == Path("."):
                    if file_name in allowed_names:
                        archive.write(file_path, arcname=str(rel_file))
                elif rel_root.parts and rel_root.parts[0] in allowed_dirs:
                    archive.write(file_path, arcname=str(rel_file))

    os.replace(temp_zip, export_zip_path)
    return export_zip_path


def move_to_trash(
    library_path: Path,
    meeting_id: str,
    base_dir: Path,
    source_url: str | None = None,
) -> None:
    """Move a lecture to the trash directory with tracking metadata instead of deleting."""
    from .bbb_import import _source_origin

    target_origin = _source_origin(source_url) if source_url else None
    recordings = load_library(library_path)
    target_rec: BBBRecording | None = None
    remaining: list[BBBRecording] = []

    for r in recordings:
        r_origin = _source_origin(r.source_url)
        matches = r.meeting_id == meeting_id and (
            target_origin is None or r_origin == target_origin
        )
        if matches and target_rec is None:
            target_rec = r
        else:
            remaining.append(r)

    if target_rec is None:
        raise ValueError(f"Лекция {meeting_id} не найдена в библиотеке.")

    trash_dir = base_dir / "trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    trash_meta_path = trash_dir / "trash.json"

    trashed_items: list[dict[str, Any]] = []
    if trash_meta_path.is_file():
        try:
            trashed_items = json.loads(trash_meta_path.read_text(encoding="utf-8"))
        except Exception:
            trashed_items = []

    lec_dir = default_lecture_directory(target_rec, base_dir=base_dir)
    if not lec_dir.is_dir() and (base_dir / target_rec.meeting_id).is_dir():
        lec_dir = base_dir / target_rec.meeting_id
    if not lec_dir.is_dir():
        # Locate caches created before lecture_id was persisted.  The legacy
        # collision suffix was deterministic, so this remains safe to migrate.
        legacy_digest = hashlib.sha256(
            repr((_source_origin(target_rec.source_url), target_rec.meeting_id)).encode("utf-8")
        ).hexdigest()[:12]
        legacy_dir = base_dir / "lectures" / f"{target_rec.meeting_id[:80]}-{legacy_digest}"
        if legacy_dir.is_dir():
            lec_dir = legacy_dir

    total_size = 0
    if lec_dir.is_dir():
        for root, _, files in os.walk(lec_dir):
            for f in files:
                try:
                    total_size += (Path(root) / f).stat().st_size
                except OSError:
                    pass

    target_trash_dir = trash_dir / lec_dir.name
    if lec_dir.is_dir():
        if target_trash_dir.exists():
            shutil.rmtree(target_trash_dir, ignore_errors=True)
        shutil.move(str(lec_dir), str(target_trash_dir))

    trashed_items.append(
        {
            "meeting_id": target_rec.meeting_id,
            "original_title": target_rec.title,
            "trashed_at": datetime.now(timezone.utc).isoformat(),
            "total_bytes": total_size,
            "source_url": target_rec.source_url,
            "audio_video_url": target_rec.audio_video_url,
            "screen_video_url": target_rec.screen_video_url,
            "slides": [
                asdict(s) if hasattr(s, "__dict__") or isinstance(s, dict) else s
                for s in target_rec.slides
            ],
            "imported_at": target_rec.imported_at,
            "folder_name": lec_dir.name,
        }
    )

    atomic_write_json(trash_meta_path, trashed_items, ensure_ascii=False, indent=2)
    save_library(remaining, path=library_path)


def restore_from_trash(
    library_path: Path,
    meeting_id: str,
    base_dir: Path,
    source_url: str | None = None,
) -> None:
    """Restore a previously trashed lecture back to the active library."""
    from .bbb_import import SlideInfo, _source_origin

    target_origin = _source_origin(source_url) if source_url else None
    trash_dir = base_dir / "trash"
    trash_meta_path = trash_dir / "trash.json"
    if not trash_meta_path.is_file():
        raise ValueError("Корзина пуста.")

    try:
        trashed_items = json.loads(trash_meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Не удалось прочитать корзину: {exc}") from exc

    target_item: dict[str, Any] | None = None
    remaining_trash: list[dict[str, Any]] = []

    for item in trashed_items:
        item_origin = _source_origin(item.get("source_url", ""))
        matches = item.get("meeting_id") == meeting_id and (
            target_origin is None or item_origin == target_origin
        )
        if matches and target_item is None:
            target_item = item
        else:
            remaining_trash.append(item)

    if target_item is None:
        raise ValueError(f"Лекция {meeting_id} не найдена в корзине.")

    folder_name = target_item.get("folder_name", meeting_id)
    trashed_dir = trash_dir / folder_name

    raw_slides = target_item.get("slides", [])
    slide_objs = tuple(
        SlideInfo(
            identifier=s.get("identifier", f"slide-{i}"),
            text=s.get("text", ""),
            image_url=s.get("image_url"),
        )
        if isinstance(s, dict)
        else s
        for i, s in enumerate(raw_slides, start=1)
    )

    restored_recording = BBBRecording(
        meeting_id=target_item["meeting_id"],
        source_url=target_item.get("source_url", ""),
        title=target_item.get("original_title", "Восстановленная лекция"),
        imported_at=target_item.get("imported_at"),
        audio_video_url=target_item.get("audio_video_url"),
        screen_video_url=target_item.get("screen_video_url"),
        slides=slide_objs,
    )

    if (base_dir / "lectures").is_dir():
        restored_dir = base_dir / "lectures" / folder_name
    else:
        restored_dir = base_dir / folder_name

    if trashed_dir.is_dir():
        if restored_dir.exists():
            shutil.rmtree(restored_dir, ignore_errors=True)
        restored_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trashed_dir), str(restored_dir))

    recordings = load_library(library_path)
    recordings.append(restored_recording)

    save_library(recordings, path=library_path)
    atomic_write_json(trash_meta_path, remaining_trash, ensure_ascii=False, indent=2)
