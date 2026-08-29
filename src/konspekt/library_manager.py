"""Library search, filtering, archival, and transactional trash operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone as dt_timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .atomic_io import AtomicIOError, atomic_write_json, atomic_write_text
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
    """Format a stored UTC timestamp in the requested local timezone."""

    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return "Дата добавления неизвестна"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    try:
        local = parsed.astimezone(timezone)
    except (OSError, OverflowError, ValueError):
        return "Дата добавления неизвестна"
    return f"Добавлено {local:%d.%m.%Y, %H:%M}"


def _quarantine_backup(library_path: Path, content: str) -> Path | None:
    timestamp = datetime.now(dt_timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = library_path.with_name(f"library-corrupt-{timestamp}.json")
    try:
        atomic_write_text(backup_path, content, encoding="utf-8")
        return backup_path
    except (AtomicIOError, OSError):
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
    """Load the library and quarantine malformed data without losing valid rows."""

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
        suffix = quarantine_path.name if quarantine_path else "не удалось записать"
        raise BBBImportError(
            f"Файл библиотеки повреждён. Резервная копия сохранена: {suffix}"
        ) from exc

    if isinstance(payload, dict):
        raw_recordings = payload.get("recordings", [])
        if not isinstance(raw_recordings, list):
            return [], _quarantine_backup(library_path, raw_text)
    elif isinstance(payload, list):
        # Backward-compatible format used by the frozen BBB importer.
        raw_recordings = payload
    else:
        return [], _quarantine_backup(library_path, raw_text)

    valid_recordings: list[BBBRecording] = []
    malformed = False
    for item in raw_recordings:
        try:
            if not isinstance(item, dict):
                raise TypeError("recording must be an object")
            valid_recordings.append(BBBRecording.from_dict(item))
        except (AttributeError, TypeError, ValueError, KeyError):
            malformed = True

    quarantine_path: Path | None = None
    if malformed:
        quarantine_path = _quarantine_backup(library_path, raw_text)
        try:
            _write_library_file(library_path, valid_recordings)
        except BBBImportError:
            pass

    sorted_recordings = sorted(
        valid_recordings, key=lambda item: item.imported_at, reverse=True
    )
    return sorted_recordings, quarantine_path


def load_library(path: Path | None = None) -> list[BBBRecording]:
    recordings, _ = load_library_with_quarantine(path)
    return recordings


def save_library(recordings: list[BBBRecording], path: Path | None = None) -> None:
    _write_library_file(path or default_library_path(), recordings)


def save_to_library(recording: BBBRecording, path: Path | None = None) -> None:
    library_path = path or default_library_path()
    identity = recording_identity(recording)
    existing = load_library(library_path)
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
    """Filter and sort recording snapshots in memory."""

    result = list(recordings)
    if query.strip():
        needle = query.strip().casefold()
        result = [recording for recording in result if needle in recording.title.casefold()]

    if state_filter is not None and base_dir is not None:
        from .local_pipeline import default_lecture_directory

        result = [
            recording
            for recording in result
            if resolve_lecture_state(
                default_lecture_directory(recording, base_dir=base_dir)
            )
            == state_filter
        ]

    if date_filter and date_filter != "all":
        now = datetime.now(dt_timezone.utc)
        filtered: list[BBBRecording] = []
        for recording in result:
            if not recording.imported_at:
                continue
            try:
                value = datetime.fromisoformat(recording.imported_at.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=dt_timezone.utc)
                age = (now - value).total_seconds()
            except (TypeError, ValueError):
                continue
            if date_filter == "today" and (value.date() == now.date() or age < 86400):
                filtered.append(recording)
            elif date_filter in {"7_days", "week"} and age <= 7 * 86400:
                filtered.append(recording)
            elif date_filter in {"30_days", "month"} and age <= 30 * 86400:
                filtered.append(recording)
        result = filtered

    if sort_by == "title_asc":
        result.sort(key=lambda item: item.title.casefold())
    elif sort_by == "title_desc":
        result.sort(key=lambda item: item.title.casefold(), reverse=True)
    elif sort_by == "date_asc":
        result.sort(key=lambda item: item.imported_at or "")
    else:
        result.sort(key=lambda item: item.imported_at or "", reverse=True)
    return result


def list_trash(base_dir: Path) -> list[dict[str, Any]]:
    trash_meta_path = base_dir / "trash" / "trash.json"
    if not trash_meta_path.is_file() or trash_meta_path.is_symlink():
        return []
    try:
        payload = json.loads(trash_meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def empty_trash(base_dir: Path) -> int:
    """Permanently remove trash contents without following symlinks."""

    trash_dir = base_dir / "trash"
    trash_meta_path = trash_dir / "trash.json"
    count = len(list_trash(base_dir))
    if trash_dir.is_dir():
        for child in trash_dir.iterdir():
            if child.name == "trash.json":
                continue
            try:
                if child.is_symlink() or child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    shutil.rmtree(child)
            except OSError as exc:
                raise BBBImportError(f"Не удалось очистить корзину: {exc}") from exc
    try:
        atomic_write_json(trash_meta_path, [], ensure_ascii=False, indent=2)
    except (AtomicIOError, OSError) as exc:
        raise BBBImportError(f"Не удалось обновить метаданные корзины: {exc}") from exc
    return count


def rename_recording(
    library_path: Path,
    meeting_id: str,
    new_title: str,
    source_url: str | None = None,
) -> list[BBBRecording]:
    clean_title = new_title.strip()
    if not clean_title:
        raise ValueError("Название лекции не может быть пустым.")

    recordings = load_library(library_path)
    target_origin = _source_origin(source_url) if source_url else None
    updated: list[BBBRecording] = []
    found = False
    for recording in recordings:
        matches = recording.meeting_id == meeting_id and (
            target_origin is None or _source_origin(recording.source_url) == target_origin
        )
        if matches:
            updated.append(
                BBBRecording(
                    meeting_id=recording.meeting_id,
                    source_url=recording.source_url,
                    title=clean_title,
                    imported_at=recording.imported_at,
                    audio_video_url=recording.audio_video_url,
                    screen_video_url=recording.screen_video_url,
                    slides=recording.slides,
                )
            )
            found = True
        else:
            updated.append(recording)
    if not found:
        raise ValueError(f"Лекция {meeting_id} не найдена в библиотеке.")
    save_library(updated, path=library_path)
    return updated


def calculate_library_size(recordings: list[BBBRecording], base_dir: Path) -> int:
    """Return active lecture size without following links or double counting files."""

    total = 0
    seen: set[Path] = set()
    for recording in recordings:
        directory = _locate_lecture_directory(recording, base_dir)
        if directory is None:
            continue
        for path in directory.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                resolved = _ensure_contained(path, base_dir)
                if resolved in seen:
                    continue
                seen.add(resolved)
                total += path.stat().st_size
            except (OSError, ValueError):
                continue
    return total


def export_lecture_archive(lecture_dir: Path, export_zip_path: Path) -> Path:
    """Create a clean archive and reject links escaping the lecture directory."""

    if not lecture_dir.is_dir() or lecture_dir.is_symlink():
        raise ValueError("Каталог лекции не существует или является ссылкой.")
    lecture_root = lecture_dir.resolve()
    export_zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = export_zip_path.with_name(f".{export_zip_path.name}.tmp")
    temporary_zip.unlink(missing_ok=True)

    allowed_names = {"lesson.md", "lesson-context.md", "transcript.json"}
    allowed_dirs = {"slides", "ocr_frames"}
    try:
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for root, directories, files in os.walk(lecture_dir, followlinks=False):
                root_path = Path(root)
                directories[:] = [
                    name for name in directories if not (root_path / name).is_symlink()
                ]
                relative_root = root_path.relative_to(lecture_dir)
                for file_name in files:
                    file_path = root_path / file_name
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    try:
                        file_path.resolve().relative_to(lecture_root)
                    except ValueError:
                        continue
                    relative_file = relative_root / file_name
                    if file_name.endswith((".part", ".tmp")):
                        continue
                    if file_name == "lecture-manifest.json":
                        continue
                    if relative_root == Path(".") and file_name in allowed_names:
                        archive.write(file_path, arcname=str(relative_file))
                    elif relative_root.parts and relative_root.parts[0] in allowed_dirs:
                        archive.write(file_path, arcname=str(relative_file))
        os.replace(temporary_zip, export_zip_path)
    except Exception:
        temporary_zip.unlink(missing_ok=True)
        raise
    return export_zip_path


def move_to_trash(
    library_path: Path,
    meeting_id: str,
    base_dir: Path,
    source_url: str | None = None,
) -> None:
    """Move a lecture and update both metadata files with rollback on failure."""

    recordings = load_library(library_path)
    target_origin = _source_origin(source_url) if source_url else None
    target: BBBRecording | None = None
    remaining: list[BBBRecording] = []
    for recording in recordings:
        matches = recording.meeting_id == meeting_id and (
            target_origin is None or _source_origin(recording.source_url) == target_origin
        )
        if matches and target is None:
            target = recording
        else:
            remaining.append(recording)
    if target is None:
        raise ValueError(f"Лекция {meeting_id} не найдена в библиотеке.")

    trash_dir = base_dir / "trash"
    trash_dir.mkdir(parents=True, exist_ok=True)
    trash_meta_path = trash_dir / "trash.json"
    trash_items = list_trash(base_dir)
    lecture_dir = _locate_lecture_directory(target, base_dir)

    total_bytes = _directory_size(lecture_dir, base_dir) if lecture_dir else 0
    original_relative_path = (
        str(lecture_dir.resolve().relative_to(base_dir.resolve())) if lecture_dir else ""
    )
    preferred_name = lecture_dir.name if lecture_dir else _safe_folder_name(target.meeting_id)
    trash_target = _unique_trash_target(trash_dir, preferred_name)

    new_item = {
        "meeting_id": target.meeting_id,
        "original_title": target.title,
        "trashed_at": datetime.now(dt_timezone.utc).isoformat(),
        "total_bytes": total_bytes,
        "source_url": target.source_url,
        "audio_video_url": target.audio_video_url,
        "screen_video_url": target.screen_video_url,
        "slides": _serialize_slides(target.slides),
        "imported_at": target.imported_at,
        "folder_name": trash_target.name,
        "original_relative_path": original_relative_path,
    }

    library_snapshot = _snapshot_text(library_path)
    trash_snapshot = _snapshot_text(trash_meta_path)
    moved = False
    try:
        if lecture_dir is not None:
            shutil.move(str(lecture_dir), str(trash_target))
        else:
            trash_target.mkdir(parents=True, exist_ok=False)
        moved = True
        atomic_write_json(trash_meta_path, [*trash_items, new_item], ensure_ascii=False, indent=2)
        save_library(remaining, path=library_path)
    except Exception as exc:
        rollback_errors = _rollback_metadata(
            library_path,
            library_snapshot,
            trash_meta_path,
            trash_snapshot,
        )
        if moved and trash_target.exists():
            try:
                if lecture_dir is not None and not lecture_dir.exists():
                    lecture_dir.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(trash_target), str(lecture_dir))
                elif lecture_dir is None:
                    shutil.rmtree(trash_target)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise BBBImportError(
                "Не удалось переместить лекцию в корзину и полностью откатить операцию: "
                + "; ".join(rollback_errors)
            ) from exc
        raise BBBImportError(f"Не удалось переместить лекцию в корзину: {exc}") from exc


def restore_from_trash(
    library_path: Path,
    meeting_id: str,
    base_dir: Path,
    source_url: str | None = None,
) -> None:
    """Restore one lecture without overwriting existing files."""

    target_origin = _source_origin(source_url) if source_url else None
    trash_dir = base_dir / "trash"
    trash_meta_path = trash_dir / "trash.json"
    trash_items = list_trash(base_dir)

    target_item: dict[str, Any] | None = None
    remaining: list[dict[str, Any]] = []
    for item in trash_items:
        item_origin = _source_origin(str(item.get("source_url", "")))
        matches = str(item.get("meeting_id", "")) == meeting_id and (
            target_origin is None or item_origin == target_origin
        )
        if matches and target_item is None:
            target_item = item
        else:
            remaining.append(item)
    if target_item is None:
        raise ValueError(f"Лекция {meeting_id} не найдена в корзине.")

    folder_name = _safe_folder_name(str(target_item.get("folder_name", "")))
    trashed_dir = _safe_child(trash_dir, folder_name)
    if not trashed_dir.is_dir() or trashed_dir.is_symlink():
        raise ValueError("Каталог лекции в корзине отсутствует или небезопасен.")

    original_relative = str(target_item.get("original_relative_path", "")).strip()
    if original_relative:
        restored_dir = _safe_relative_child(base_dir, original_relative)
    elif (base_dir / "lectures").is_dir():
        restored_dir = _safe_child(base_dir / "lectures", folder_name)
    else:
        restored_dir = _safe_child(base_dir, folder_name)
    if restored_dir.exists():
        raise ValueError(
            f"Восстановление остановлено: каталог уже существует — {restored_dir.name}."
        )

    restored_recording = _recording_from_trash(target_item)
    current_library = load_library(library_path)
    if any(
        recording_identity(item) == recording_identity(restored_recording)
        for item in current_library
    ):
        raise ValueError("В библиотеке уже существует эта лекция; данные не перезаписаны.")

    library_snapshot = _snapshot_text(library_path)
    trash_snapshot = _snapshot_text(trash_meta_path)
    moved = False
    try:
        restored_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trashed_dir), str(restored_dir))
        moved = True
        save_library([restored_recording, *current_library], path=library_path)
        atomic_write_json(trash_meta_path, remaining, ensure_ascii=False, indent=2)
    except Exception as exc:
        rollback_errors = _rollback_metadata(
            library_path,
            library_snapshot,
            trash_meta_path,
            trash_snapshot,
        )
        if moved and restored_dir.exists() and not trashed_dir.exists():
            try:
                trash_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(restored_dir), str(trashed_dir))
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise BBBImportError(
                "Не удалось восстановить лекцию и полностью откатить операцию: "
                + "; ".join(rollback_errors)
            ) from exc
        raise BBBImportError(f"Не удалось восстановить лекцию: {exc}") from exc


def _serialize_slides(slides: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for slide in slides or ():
        if isinstance(slide, dict):
            output.append(
                {
                    "identifier": str(slide.get("identifier", "")),
                    "text": str(slide.get("text", "")),
                    "image_url": slide.get("image_url"),
                }
            )
        else:
            try:
                output.append(asdict(slide))
            except TypeError:
                continue
    return output


def _recording_from_trash(item: dict[str, Any]) -> BBBRecording:
    raw_slides = item.get("slides", [])
    if not isinstance(raw_slides, list):
        raw_slides = []
    slides = tuple(
        SlideInfo(
            identifier=str(slide.get("identifier", f"slide-{index}")),
            text=str(slide.get("text", "")),
            image_url=slide.get("image_url"),
        )
        for index, slide in enumerate(raw_slides, start=1)
        if isinstance(slide, dict)
    )
    meeting_id = str(item.get("meeting_id", "")).strip()
    source_url = str(item.get("source_url", "")).strip()
    audio_url = str(item.get("audio_video_url", "")).strip()
    if not meeting_id or not source_url or not audio_url:
        raise ValueError("Метаданные лекции в корзине повреждены.")
    return BBBRecording(
        meeting_id=meeting_id,
        source_url=source_url,
        title=str(item.get("original_title", "Восстановленная лекция")),
        imported_at=str(item.get("imported_at", "")),
        audio_video_url=audio_url,
        screen_video_url=item.get("screen_video_url"),
        slides=slides,
    )


def _locate_lecture_directory(recording: BBBRecording, base_dir: Path) -> Path | None:
    from .local_pipeline import default_lecture_directory

    try:
        primary = default_lecture_directory(recording, base_dir=base_dir)
    except Exception:
        primary = base_dir / "lectures" / _safe_folder_name(recording.meeting_id)
    candidates = [primary, base_dir / recording.meeting_id]
    digest = hashlib.sha256(
        repr((_source_origin(recording.source_url), recording.meeting_id)).encode("utf-8")
    ).hexdigest()[:12]
    candidates.append(base_dir / "lectures" / f"{recording.meeting_id[:80]}-{digest}")
    for candidate in candidates:
        try:
            _ensure_contained(candidate, base_dir)
        except ValueError:
            continue
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return None


def _directory_size(directory: Path | None, base_dir: Path) -> int:
    if directory is None:
        return 0
    total = 0
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            _ensure_contained(path, base_dir)
            total += path.stat().st_size
        except (OSError, ValueError):
            continue
    return total


def _unique_trash_target(trash_dir: Path, preferred_name: str) -> Path:
    safe_name = _safe_folder_name(preferred_name)
    candidate = _safe_child(trash_dir, safe_name)
    if not candidate.exists():
        return candidate
    suffix = datetime.now(dt_timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return _safe_child(trash_dir, f"{safe_name}-{suffix}")


def _safe_folder_name(value: str) -> str:
    name = value.strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or Path(name).name != name
    ):
        raise ValueError("Небезопасное имя каталога в метаданных корзины.")
    return name


def _safe_child(base: Path, name: str) -> Path:
    candidate = base / _safe_folder_name(name)
    _ensure_contained(candidate, base)
    return candidate


def _safe_relative_child(base: Path, relative: str) -> Path:
    value = relative.strip()
    if not value or "\x00" in value or Path(value).is_absolute():
        raise ValueError("Небезопасный исходный путь в метаданных корзины.")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Небезопасный исходный путь в метаданных корзины.")
    candidate = base.joinpath(*parts)
    _ensure_contained(candidate, base)
    return candidate


def _ensure_contained(path: Path, base: Path) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(base.resolve(strict=False))
    except ValueError as exc:
        raise ValueError("Путь выходит за пределы каталога приложения.") from exc
    return resolved


def _snapshot_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _restore_snapshot(path: Path, snapshot: str | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_text(path, snapshot, encoding="utf-8")


def _rollback_metadata(
    library_path: Path,
    library_snapshot: str | None,
    trash_path: Path,
    trash_snapshot: str | None,
) -> list[str]:
    errors: list[str] = []
    for path, snapshot in (
        (library_path, library_snapshot),
        (trash_path, trash_snapshot),
    ):
        try:
            _restore_snapshot(path, snapshot)
        except (AtomicIOError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")
    return errors
