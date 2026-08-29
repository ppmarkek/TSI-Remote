"""Library operations: searching, filtering, renaming, exporting, and safe trashing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .atomic_io import AtomicIOError, atomic_write_json
from .bbb_import import BBBImportError, BBBRecording, SlideInfo
from .workflow import LectureState, resolve_lecture_state

LIBRARY_SCHEMA_VERSION = 1


def default_library_path() -> Path:
    """Keep study metadata in the platform application-data directory."""
    from .platform_services import PlatformAppPaths

    return PlatformAppPaths().data_dir / "library.json"


def _source_origin(source_url: str) -> str:
    parsed = urlparse(source_url.strip())
    host = (parsed.hostname or "").casefold()
    if not host:
        return source_url.strip().casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    scheme = parsed.scheme.casefold()
    prefix = f"{scheme}://" if scheme else ""
    if port and port not in {80, 443}:
        host = f"{host}:{port}"
    return f"{prefix}{host}"


def recording_identity(recording: BBBRecording) -> tuple[str, str]:
    """Identify a recording within its BBB server, not across unrelated hosts."""
    return (_source_origin(recording.source_url), recording.meeting_id)


def format_imported_at(value: str, *, timezone: tzinfo | None = None) -> str:
    """Format a stored UTC timestamp in the user's local timezone."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return "Дата добавления неизвестна"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        local = parsed.astimezone(timezone)
    except (OSError, OverflowError, ValueError):
        return "Дата добавления неизвестна"
    return f"Добавлено {local:%d.%m.%Y, %H:%M}"


def _quarantine_backup(library_path: Path, content: str) -> Path | None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = library_path.with_name(f"library-corrupt-{timestamp}.json")
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(content, encoding="utf-8")
        return backup_path
    except OSError:
        return None


def _write_library_file(library_path: Path, recordings: list[BBBRecording]) -> None:
    payload = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "recordings": [item.to_dict() for item in recordings],
    }
    try:
        atomic_write_json(library_path, payload, ensure_ascii=False, indent=2)
    except (AtomicIOError, OSError) as exc:
        raise BBBImportError(f"Не удалось сохранить библиотеку лекций: {exc}") from exc


def load_library_with_quarantine(
    path: Path | None = None,
) -> tuple[list[BBBRecording], Path | None]:
    """Load library and quarantine corrupted entries if any exist."""
    library_path = path or default_library_path()
    if not library_path.is_file():
        return [], None

    try:
        raw_text = library_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BBBImportError("Не удалось прочитать локальную библиотеку лекций.") from exc

    if not raw_text.strip():
        return [], None

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        quarantine_path = _quarantine_backup(library_path, raw_text)
        raise BBBImportError(
            f"Файл библиотеки повреждён. Резервная копия сохранена: {quarantine_path.name if quarantine_path else 'не удалось записать'}"
        ) from exc

    raw_recordings: list[Any]
    if isinstance(payload, dict):
        raw_recordings = payload.get("recordings", [])
        if not isinstance(raw_recordings, list):
            quarantine_path = _quarantine_backup(library_path, raw_text)
            return [], quarantine_path
    elif isinstance(payload, list):
        raw_recordings = payload
    else:
        quarantine_path = _quarantine_backup(library_path, raw_text)
        return [], quarantine_path

    valid_recordings: list[BBBRecording] = []
    has_malformed = False

    for item in raw_recordings:
        try:
            recording = BBBRecording.from_dict(item)
            valid_recordings.append(recording)
        except (TypeError, ValueError, KeyError):
            has_malformed = True

    quarantine_path: Path | None = None
    if has_malformed:
        quarantine_path = _quarantine_backup(library_path, raw_text)
        try:
            _write_library_file(library_path, valid_recordings)
        except (AtomicIOError, OSError):
            pass

    sorted_recordings = sorted(valid_recordings, key=lambda item: item.imported_at, reverse=True)
    return sorted_recordings, quarantine_path


def load_library(path: Path | None = None) -> list[BBBRecording]:
    """Return locally saved recordings, newest first, quarantining corrupt rows."""
    recordings, _ = load_library_with_quarantine(path)
    return recordings


def save_library(recordings: list[BBBRecording], path: Path | None = None) -> None:
    """Save a list of recordings atomically to library.json."""
    library_path = path or default_library_path()
    _write_library_file(library_path, recordings)


def save_to_library(recording: BBBRecording, path: Path | None = None) -> None:
    """Persist one recording atomically, replacing only the same recording from the same BBB."""
    library_path = path or default_library_path()
    existing = load_library(library_path)
    identity = recording_identity(recording)
    updated = [item for item in existing if recording_identity(item) != identity]
    updated.insert(0, recording)
    _write_library_file(library_path, updated)


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
    date_filter: str | None = None,
    base_dir: Path | None = None,
) -> list[BBBRecording]:
    """Filter and sort recording snapshots in-memory without repetitive filesystem queries."""
    result = list(recordings)

    if query.strip():
        q = query.strip().lower()
        result = [r for r in result if q in r.title.lower()]

    if state_filter is not None and base_dir is not None:
        from .local_pipeline import default_lecture_directory

        result = [
            r
            for r in result
            if resolve_lecture_state(default_lecture_directory(r, base_dir=base_dir))
            == state_filter
        ]

    if date_filter and date_filter != "all":
        now = datetime.now(timezone.utc)
        filtered_by_date: list[BBBRecording] = []
        for r in result:
            if not r.imported_at:
                continue
            try:
                dt = datetime.fromisoformat(r.imported_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_seconds = (now - dt).total_seconds()
                if date_filter == "today" and (dt.date() == now.date() or age_seconds < 86400):
                    filtered_by_date.append(r)
                elif date_filter in {"7_days", "week"} and age_seconds <= 7 * 86400:
                    filtered_by_date.append(r)
                elif date_filter in {"30_days", "month"} and age_seconds <= 30 * 86400:
                    filtered_by_date.append(r)
            except (TypeError, ValueError):
                pass
        result = filtered_by_date

    if sort_by == "title_asc":
        result.sort(key=lambda r: r.title.lower())
    elif sort_by == "title_desc":
        result.sort(key=lambda r: r.title.lower(), reverse=True)
    elif sort_by == "date_asc":
        result.sort(key=lambda r: r.imported_at or "")
    else:  # default date_desc
        result.sort(key=lambda r: r.imported_at or "", reverse=True)

    return result


def list_trash(base_dir: Path) -> list[dict[str, Any]]:
    """Return all items currently in the trash."""
    trash_meta_path = base_dir / "trash" / "trash.json"
    if not trash_meta_path.is_file():
        return []
    try:
        data = json.loads(trash_meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def empty_trash(base_dir: Path) -> int:
    """Permanently delete all contents of the trash directory."""
    trash_dir = base_dir / "trash"
    trash_meta_path = trash_dir / "trash.json"
    items = list_trash(base_dir)
    count = len(items)

    if trash_dir.is_dir():
        for child in trash_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.name != "trash.json":
                try:
                    child.unlink(missing_ok=True)
                except OSError:
                    pass

    atomic_write_json(trash_meta_path, [], ensure_ascii=False, indent=2)
    return count


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
    from .local_pipeline import default_lecture_directory

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
    from .local_pipeline import default_lecture_directory

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
