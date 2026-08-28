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


class ManifestError(RuntimeError):
    """The lecture manifest could not be loaded, verified, or updated."""


def compute_lecture_id(source_url: str, meeting_id: str) -> str:
    """Generate a stable, unique lecture ID based on normalized origin and meeting ID."""
    clean_meeting = re.sub(r"[^\w\-.]", "_", meeting_id.strip())
    try:
        parsed = urlparse(source_url)
        origin = f"{parsed.scheme}://{parsed.netloc}".strip().lower()
    except Exception:
        origin = source_url.strip().lower()

    combined = f"{origin}:{meeting_id.strip()}".encode()
    origin_hash = hashlib.sha256(combined).hexdigest()[:12]
    return f"{clean_meeting[:40]}-{origin_hash}"


def file_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file on disk."""
    if not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass
class ManifestStage:
    """Represents the execution state, parameters, and artifact hashes for a pipeline stage."""

    status: str = "pending"
    completed_at: str | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)

    def is_valid(self, expected_fingerprint: dict[str, Any], base_dir: Path) -> bool:
        """Verify that the stage succeeded with identical parameters and all output files are intact."""
        if self.status != "completed":
            return False
        if self.fingerprint != expected_fingerprint:
            return False
        if not self.outputs:
            return False
        for rel_path, expected_hash in self.outputs.items():
            target_path = base_dir / rel_path
            if not target_path.is_file():
                return False
            actual_hash = file_sha256(target_path)
            if actual_hash != expected_hash:
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
        if stage is None:
            return False
        return stage.is_valid(expected_fingerprint, base_dir)

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
        if not path.is_file():
            raise ManifestError(f"Файл манифеста не существует: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError("Не удалось прочитать файл манифеста.") from exc

        if not isinstance(payload, dict):
            raise ManifestError("Повреждённый формат манифеста.")

        lecture_id = str(payload.get("lecture_id", "")).strip()
        meeting_id = str(payload.get("meeting_id", "")).strip()
        source_url = str(payload.get("source_url", "")).strip()
        if not lecture_id or not meeting_id:
            raise ManifestError("Манифест не содержит обязательных идентификаторов.")

        stages_raw = payload.get("stages", {})
        stages: dict[str, ManifestStage] = {}
        if isinstance(stages_raw, dict):
            for name, data in stages_raw.items():
                if isinstance(data, dict):
                    stages[str(name)] = ManifestStage(
                        status=str(data.get("status", "pending")),
                        completed_at=data.get("completed_at"),
                        fingerprint=data.get("fingerprint", {})
                        if isinstance(data.get("fingerprint"), dict)
                        else {},
                        outputs=data.get("outputs", {})
                        if isinstance(data.get("outputs"), dict)
                        else {},
                    )

        return cls(
            lecture_id=lecture_id,
            meeting_id=meeting_id,
            source_url=source_url,
            schema_version=int(payload.get("schema_version", MANIFEST_SCHEMA_VERSION)),
            created_at=str(payload.get("created_at", "")),
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
        manifest_path = directory / "lecture-manifest.json"
        if manifest_path.is_file():
            try:
                return cls.load(manifest_path)
            except ManifestError:
                pass
        lecture_id = compute_lecture_id(source_url, meeting_id)
        manifest = cls(
            lecture_id=lecture_id,
            meeting_id=meeting_id,
            source_url=source_url,
        )
        return manifest
