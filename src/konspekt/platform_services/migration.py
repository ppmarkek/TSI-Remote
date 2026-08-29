"""One-time safe data migration from legacy paths with hash verification and receipt."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..atomic_io import atomic_write_json
from .interfaces import AppPaths


class MigrationStatus(str, Enum):
    ALREADY_MIGRATED = "already_migrated"
    NO_LEGACY_DATA = "no_legacy_data"
    COMPLETED = "completed"
    CONFLICT = "conflict"
    ERROR = "error"


@dataclass(frozen=True)
class MigrationResult:
    status: MigrationStatus
    source_path: Path | None
    destination_path: Path
    receipt_path: Path | None = None
    copied_files_count: int = 0
    error_message: str | None = None


def find_legacy_source_dir(system: str | None = None, home_dir: Path | None = None) -> Path | None:
    """Identify legacy storage locations that require migration to standard OS directories."""
    target_system = system or sys.platform
    home = home_dir or Path.home()

    if target_system == "darwin":
        erroneous_mac_path = home / "AppData" / "Local" / "Konspekt"
        if erroneous_mac_path.is_dir() and (erroneous_mac_path / "library.json").is_file():
            return erroneous_mac_path

    return None


def calculate_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def migrate_legacy_data(
    app_paths: AppPaths,
    *,
    system: str | None = None,
    legacy_source: Path | None = None,
    home_dir: Path | None = None,
) -> MigrationResult:
    """Safely migrate user library data into the platform data directory."""
    dest_dir = app_paths.data_dir
    receipt_path = dest_dir / "migration-receipt.json"

    if receipt_path.is_file():
        return MigrationResult(
            status=MigrationStatus.ALREADY_MIGRATED,
            source_path=None,
            destination_path=dest_dir,
            receipt_path=receipt_path,
        )

    source_dir = legacy_source or find_legacy_source_dir(system=system, home_dir=home_dir)
    if source_dir is None or not source_dir.is_dir() or not source_dir.exists():
        return MigrationResult(
            status=MigrationStatus.NO_LEGACY_DATA,
            source_path=None,
            destination_path=dest_dir,
        )

    if source_dir.resolve() == dest_dir.resolve():
        return MigrationResult(
            status=MigrationStatus.NO_LEGACY_DATA,
            source_path=source_dir,
            destination_path=dest_dir,
        )

    # Conflict check: if destination already has a library.json with distinct content
    source_lib = source_dir / "library.json"
    dest_lib = dest_dir / "library.json"
    if dest_lib.is_file() and source_lib.is_file():
        try:
            dest_bytes = dest_lib.read_bytes().strip()
            src_bytes = source_lib.read_bytes().strip()
            if dest_bytes and src_bytes and dest_bytes != src_bytes:
                return MigrationResult(
                    status=MigrationStatus.CONFLICT,
                    source_path=source_dir,
                    destination_path=dest_dir,
                    error_message=(
                        f"Обнаружен конфликт: библиотеки существуют и в {source_dir}, "
                        f"и в {dest_dir} с разным содержимым."
                    ),
                )
        except OSError as exc:
            return MigrationResult(
                status=MigrationStatus.ERROR,
                source_path=source_dir,
                destination_path=dest_dir,
                error_message=f"Ошибка чтения при проверке конфликта: {exc}",
            )

    staging_dir = dest_dir / ".migration-staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)

    dest_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    file_hashes: dict[str, str] = {}
    copied_count = 0

    try:
        # Collect source files
        source_files = [p for p in source_dir.rglob("*") if p.is_file()]
        for src_file in source_files:
            rel_path = src_file.relative_to(source_dir)
            target_staging_file = staging_dir / rel_path
            target_staging_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target_staging_file)

            src_hash = calculate_sha256(src_file)
            staging_hash = calculate_sha256(target_staging_file)

            if src_hash != staging_hash:
                shutil.rmtree(staging_dir, ignore_errors=True)
                return MigrationResult(
                    status=MigrationStatus.ERROR,
                    source_path=source_dir,
                    destination_path=dest_dir,
                    error_message=f"Несовпадение контрольной суммы для {rel_path}",
                )

            file_hashes[str(rel_path)] = src_hash
            copied_count += 1

        # Move files from staging into final destination
        staging_files = [p for p in staging_dir.rglob("*") if p.is_file()]
        for stg_file in staging_files:
            rel_path = stg_file.relative_to(staging_dir)
            final_target = dest_dir / rel_path
            final_target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stg_file, final_target)

        # Write migration receipt
        receipt_payload = {
            "migration_version": 1,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(source_dir),
            "destination_directory": str(dest_dir),
            "file_count": copied_count,
            "files": file_hashes,
        }
        atomic_write_json(receipt_path, receipt_payload)

    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return MigrationResult(
            status=MigrationStatus.ERROR,
            source_path=source_dir,
            destination_path=dest_dir,
            error_message=f"Сбой во время переноса файлов: {exc}",
        )
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)

    return MigrationResult(
        status=MigrationStatus.COMPLETED,
        source_path=source_dir,
        destination_path=dest_dir,
        receipt_path=receipt_path,
        copied_files_count=copied_count,
    )
