"""Durable, verifiable stage manifests with parameter and content fingerprinting."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .atomic_io import AtomicIOError, atomic_write_json

MANIFEST_SCHEMA_VERSION = 1
ALGORITHM_VERSION = "1.0.0"
_VALID_STAGE_STATUSES = {"pending", "completed", "failed"}


class ManifestError(RuntimeError):
    """The lecture manifest could not be loaded, verified, or updated."""


def compute_lecture_id(source_url: str, meeting_id: str) -> str:
    """Generate a stable, unique lecture ID from normalized origin and meeting ID."""

    clean_meeting = re.sub(r"[^\w\-.]", "_", meeting_id.strip()) or "lecture"
    try:
        parsed = urlparse(source_url)
        host = (parsed.hostname or parsed.netloc).strip().lower()
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port and port not in {80, 443}:
            host = f"{host}:{port}"
        origin = f"{parsed.scheme.lower()}://{host}".strip()
    except Exception:
        origin = source_url.strip().lower()

    combined = f"{origin}:{meeting_id.strip()}".encode("utf-8")
    origin_hash = hashlib.sha256(combined).hexdigest()[:12]
    return f"{clean_meeting[:40]}-{origin_hash}"


def file_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a regular, non-symlink file."""

    if not path.is_file() or path.is_symlink():
        return ""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


@dataclass
class ManifestStage:
    """Execution state, parameters, and artifact hashes for one pipeline stage."""

    status: str = "pending"
    completed_at: str | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    def is_valid(self, expected_fingerprint: dict[str, Any], base_dir: Path) -> bool:
        if self.status != "completed" or self.fingerprint != expected_fingerprint:
            return False
        if not self.outputs:
            return False

        base = base_dir.resolve()
        for relative_name, expected_hash in self.outputs.items():
            if not expected_hash or not _is_safe_relative_path(relative_name):
                return False
            candidate = base_dir / relative_name
            if candidate.is_symlink():
                return False
            target = candidate.resolve(strict=False)
            try:
                target.relative_to(base)
            except ValueError:
                return False
            if not target.is_file():
                return False
            if file_sha256(target) != expected_hash:
                return False
        return True


@dataclass
class LectureManifest:
    """Versioned metadata tracking all stages, inputs, and artifacts of a lecture."""

    lecture_id: str
    meeting_id: str
    source_url: str
    schema_version: int = MANIFEST_SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stages: dict[str, ManifestStage] = field(default_factory=dict)

    def get_stage(self, stage_name: str) -> ManifestStage:
        if stage_name not in self.stages:
            self.stages[stage_name] = ManifestStage()
        return self.stages[stage_name]

    def is_stage_valid(
        self,
        stage_name: str,
        expected_fingerprint: dict[str, Any],
        base_dir: Path,
    ) -> bool:
        stage = self.stages.get(stage_name)
        return bool(stage and stage.is_valid(expected_fingerprint, base_dir))

    def record_stage_success(
        self,
        stage_name: str,
        fingerprint: dict[str, Any],
        outputs: dict[str, str],
    ) -> None:
        self.stages[stage_name] = ManifestStage(
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            fingerprint=fingerprint,
            outputs=outputs,
        )

    def record_stage_failure(self, stage_name: str, fingerprint: dict[str, Any]) -> None:
        self.stages[stage_name] = ManifestStage(
            status="failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            fingerprint=fingerprint,
            outputs={},
        )

    def invalidate_stage(self, stage_name: str) -> None:
        self.stages.pop(stage_name, None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lecture_id": self.lecture_id,
            "meeting_id": self.meeting_id,
            "source_url": self.source_url,
            "created_at": self.created_at,
            "stages": {name: asdict(stage) for name, stage in self.stages.items()},
        }

    def save(self, path: Path) -> None:
        try:
            atomic_write_json(path, self.to_dict(), ensure_ascii=False, indent=2)
        except (AtomicIOError, OSError) as exc:
            raise ManifestError(f"Не удалось сохранить манифест лекции: {exc}") from exc

    @classmethod
    def load(cls, path: Path) -> LectureManifest:
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"Файл манифеста не существует: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError("Не удалось прочитать файл манифеста.") from exc
        if not isinstance(payload, dict):
            raise ManifestError("Повреждённый формат манифеста.")

        schema_version = payload.get("schema_version", MANIFEST_SCHEMA_VERSION)
        if type(schema_version) is not int or schema_version != MANIFEST_SCHEMA_VERSION:
            raise ManifestError("Неподдерживаемая версия схемы манифеста.")

        lecture_id = _required_string(payload, "lecture_id")
        meeting_id = _required_string(payload, "meeting_id")
        source_url = str(payload.get("source_url", "")).strip()
        created_at = str(payload.get("created_at", "")).strip()

        stages_raw = payload.get("stages", {})
        if not isinstance(stages_raw, dict):
            raise ManifestError("Поле stages в манифесте повреждено.")

        stages: dict[str, ManifestStage] = {}
        for raw_name, raw_stage in stages_raw.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_stage, dict):
                raise ManifestError("Манифест содержит повреждённый этап.")

            status = str(raw_stage.get("status", "pending"))
            if status not in _VALID_STAGE_STATUSES:
                raise ManifestError(f"Этап {name} содержит неизвестный статус.")

            completed_at_raw = raw_stage.get("completed_at")
            if completed_at_raw is not None and not isinstance(completed_at_raw, str):
                raise ManifestError(f"Этап {name} содержит неверную дату завершения.")

            fingerprint = raw_stage.get("fingerprint", {})
            outputs = raw_stage.get("outputs", {})
            if not isinstance(fingerprint, dict) or not isinstance(outputs, dict):
                raise ManifestError(f"Этап {name} содержит неверные fingerprint/outputs.")

            normalized_outputs: dict[str, str] = {}
            for raw_path, raw_hash in outputs.items():
                relative_path = str(raw_path)
                digest = str(raw_hash)
                if not _is_safe_relative_path(relative_path) or not digest:
                    raise ManifestError(f"Этап {name} содержит небезопасный output.")
                normalized_outputs[relative_path] = digest

            stages[name] = ManifestStage(
                status=status,
                completed_at=completed_at_raw,
                fingerprint=fingerprint,
                outputs=normalized_outputs,
            )

        return cls(
            lecture_id=lecture_id,
            meeting_id=meeting_id,
            source_url=source_url,
            schema_version=schema_version,
            created_at=created_at,
            stages=stages,
        )

    @classmethod
    def for_recording(
        cls,
        recording_title: str,
        meeting_id: str,
        source_url: str,
        directory: Path,
    ) -> LectureManifest:
        del recording_title  # Reserved for future schema versions.
        manifest_path = directory / "lecture-manifest.json"
        lecture_id = compute_lecture_id(source_url, meeting_id)
        if manifest_path.is_file():
            try:
                loaded = cls.load(manifest_path)
                if loaded.lecture_id == lecture_id and loaded.meeting_id == meeting_id:
                    return loaded
            except ManifestError:
                pass
        return cls(
            lecture_id=lecture_id,
            meeting_id=meeting_id,
            source_url=source_url,
        )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Манифест не содержит обязательного поля {key}.")
    return value.strip()


def _is_safe_relative_path(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return True
