"""Inspect public BigBlueButton playback links without downloading whole videos."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests

from .atomic_io import AtomicIOError, atomic_write_json
from .bbb_download import RecordingInfo, parse_playback_url

LIBRARY_SCHEMA_VERSION = 1


class BBBImportError(RuntimeError):
    """A playback link could not be imported into the lecture library."""


@dataclass(frozen=True)
class SlideInfo:
    """Text and an optional image source for one recorded slide."""

    identifier: str
    text: str
    image_url: str | None = None


@dataclass(frozen=True)
class BBBRecording:
    """The lightweight, local record of a BBB playback source."""

    meeting_id: str
    source_url: str
    title: str
    imported_at: str
    audio_video_url: str
    screen_video_url: str | None
    slides: tuple[SlideInfo, ...]

    @property
    def has_screen_share(self) -> bool:
        return self.screen_video_url is not None

    @property
    def has_slide_text(self) -> bool:
        return any(slide.text.strip() for slide in self.slides)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BBBRecording:
        if not isinstance(payload, dict):
            raise ValueError("Данные записи должны быть словарём.")
        meeting_id = str(payload.get("meeting_id", "")).strip()
        source_url = str(payload.get("source_url", "")).strip()
        title = str(payload.get("title", "")).strip()
        imported_at = str(payload.get("imported_at", "")).strip()
        audio_video_url = str(payload.get("audio_video_url", "")).strip()
        if not (meeting_id and source_url and title and imported_at and audio_video_url):
            raise ValueError("Запись не содержит обязательных полей.")
        raw_slides = payload.get("slides")
        slides: list[SlideInfo] = []
        if isinstance(raw_slides, (list, tuple)):
            for item in raw_slides:
                if isinstance(item, dict):
                    ident = str(item.get("identifier", "")).strip() or "slide"
                    text = str(item.get("text", ""))
                    img = item.get("image_url")
                    slides.append(
                        SlideInfo(identifier=ident, text=text, image_url=str(img) if img else None)
                    )
        return cls(
            meeting_id=meeting_id,
            source_url=source_url,
            title=title,
            imported_at=imported_at,
            audio_video_url=audio_video_url,
            screen_video_url=payload.get("screen_video_url"),
            slides=tuple(slides),
        )


WEBCAM_PATHS = ("video/webcams.mp4", "video/webcams.webm")
DESKSHARE_PATHS = ("deskshare/deskshare.mp4", "deskshare/deskshare.webm")


def inspect_bbb_recording(
    playback_url: str,
    *,
    session: requests.Session | Any | None = None,
) -> BBBRecording:
    """Find the playback assets that can later be used to build a lesson."""

    try:
        info = parse_playback_url(playback_url.strip())
    except ValueError as exc:
        raise BBBImportError(str(exc)) from exc

    client = session or requests.Session()
    webcam_url = _first_available(client, info, WEBCAM_PATHS)
    if webcam_url is None:
        raise BBBImportError(
            "В записи не найден поток с камерой и звуком. Проверь ссылку или доступ к записи."
        )

    deskshare_url = _first_available(client, info, DESKSHARE_PATHS)
    metadata = _fetch_optional_text(client, _asset_url(info, "metadata.xml"))
    slide_text = _fetch_optional_json(
        client,
        _asset_url(info, "presentation_text.json"),
    )
    slides_timeline = _fetch_optional_text(client, _asset_url(info, "slides_new.xml"))

    title = _title_from_metadata(metadata) or f"Лекция {info.meeting_id[-8:]}"
    slides = _merge_slides(slide_text, slides_timeline, info)
    return BBBRecording(
        meeting_id=info.meeting_id,
        source_url=playback_url.strip(),
        title=title,
        imported_at=datetime.now(UTC).isoformat(),
        audio_video_url=webcam_url,
        screen_video_url=deskshare_url,
        slides=slides,
    )


def load_library(path: Path | None = None) -> list[BBBRecording]:
    """Return locally saved recordings, newest first, quarantining corrupt rows."""

    recordings, _ = load_library_with_quarantine(path)
    return recordings


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
        # Resave the sanitized valid records
        try:
            _write_library_file(library_path, valid_recordings)
        except (AtomicIOError, OSError):
            pass

    sorted_recordings = sorted(valid_recordings, key=lambda item: item.imported_at, reverse=True)
    return sorted_recordings, quarantine_path


def save_to_library(recording: BBBRecording, path: Path | None = None) -> None:
    """Persist one recording atomically, replacing only the same recording from the same BBB."""

    library_path = path or default_library_path()
    existing = load_library(library_path)
    identity = recording_identity(recording)
    updated = [item for item in existing if recording_identity(item) != identity]
    updated.insert(0, recording)

    _write_library_file(library_path, updated)


def _write_library_file(library_path: Path, recordings: list[BBBRecording]) -> None:
    payload = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "recordings": [item.to_dict() for item in recordings],
    }
    try:
        atomic_write_json(library_path, payload, ensure_ascii=False, indent=2)
    except (AtomicIOError, OSError) as exc:
        raise BBBImportError(f"Не удалось сохранить библиотеку лекций: {exc}") from exc


def save_library(recordings: list[BBBRecording], path: Path | None = None) -> None:
    """Save a list of recordings atomically to library.json."""
    library_path = path or default_library_path()
    _write_library_file(library_path, recordings)


def _quarantine_backup(library_path: Path, content: str) -> Path | None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = library_path.with_name(f"library-corrupt-{timestamp}.json")
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(content, encoding="utf-8")
        return backup_path
    except OSError:
        return None


def default_library_path() -> Path:
    """Keep study metadata in the platform application-data directory."""
    from .platform_services import PlatformAppPaths

    return PlatformAppPaths().data_dir / "library.json"


def recording_identity(recording: BBBRecording) -> tuple[str, str]:
    """Identify a recording within its BBB server, not across unrelated hosts."""

    return (_source_origin(recording.source_url), recording.meeting_id)


def _source_origin(source_url: str) -> str:
    parsed = urlparse(source_url.strip())
    host = (parsed.hostname or "").casefold()
    if not host:
        return source_url.strip().casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port and port not in {80, 443}:
        return f"{host}:{port}"
    return host


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


def _asset_url(info: RecordingInfo, relative_path: str) -> str:
    return f"{info.base_url}/{relative_path}"


def _first_available(
    session: requests.Session | Any,
    info: RecordingInfo,
    relative_paths: tuple[str, ...],
) -> str | None:
    for relative_path in relative_paths:
        url = _asset_url(info, relative_path)
        try:
            response = session.head(url, timeout=20, allow_redirects=True)
        except requests.RequestException:
            continue
        if response.status_code < 400:
            return url
    return None


def _fetch_optional_text(session: requests.Session | Any, url: str) -> str | None:
    try:
        response = session.get(url, timeout=20)
    except requests.RequestException:
        return None
    if response.status_code >= 400:
        return None
    return response.text


def _fetch_optional_json(session: requests.Session | Any, url: str) -> dict[str, Any]:
    text = _fetch_optional_text(session, url)
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _title_from_metadata(metadata: str | None) -> str | None:
    if not metadata:
        return None
    try:
        root = ElementTree.fromstring(metadata)
    except ElementTree.ParseError:
        return None
    meeting = root.find(".//meeting")
    if meeting is None:
        return None
    title = meeting.attrib.get("name", "").strip()
    return title or None


def _merge_slides(
    slide_text: dict[str, Any],
    slides_timeline: str | None,
    info: RecordingInfo,
) -> tuple[SlideInfo, ...]:
    slides: dict[str, SlideInfo] = {}
    for presentation in slide_text.values():
        if not isinstance(presentation, dict):
            continue
        for identifier, text in presentation.items():
            slides[str(identifier)] = SlideInfo(
                identifier=str(identifier),
                text=str(text or ""),
            )

    if slides_timeline:
        try:
            root = ElementTree.fromstring(slides_timeline)
        except ElementTree.ParseError:
            root = None
        if root is not None:
            for element in root.iter():
                if _local_name(element.tag).lower() not in {"slide", "image"}:
                    continue
                identifier = (
                    element.attrib.get("id")
                    or element.attrib.get("slide")
                    or element.attrib.get("name")
                )
                source = (
                    element.attrib.get("url")
                    or element.attrib.get("src")
                    or element.attrib.get("href")
                    or element.attrib.get("image")
                )
                if not identifier or not source:
                    continue
                image_url = urljoin(f"{info.base_url}/", source)
                previous = slides.get(identifier)
                slides[identifier] = SlideInfo(
                    identifier=identifier,
                    text=previous.text if previous else "",
                    image_url=image_url,
                )

    return tuple(slides.values())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]
