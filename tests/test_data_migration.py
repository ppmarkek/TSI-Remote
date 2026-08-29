from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.platform_services import (
    MigrationStatus,
    PlatformAppPaths,
    migrate_legacy_data,
)


class DataMigrationTests(unittest.TestCase):
    def test_no_legacy_data_is_a_clean_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dest_paths = PlatformAppPaths(test_root=Path(temp_dir) / "app")
            result = migrate_legacy_data(
                dest_paths, legacy_source=Path(temp_dir) / "missing_source"
            )

            self.assertEqual(result.status, MigrationStatus.NO_LEGACY_DATA)
            self.assertFalse((dest_paths.data_dir / "migration-receipt.json").exists())

    def test_clean_migration_copies_verifies_and_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "legacy_source"
            source_dir.mkdir()
            (source_dir / "library.json").write_text(
                json.dumps([{"meeting_id": "test"}]), encoding="utf-8"
            )
            (source_dir / "settings.json").write_text(
                json.dumps({"whisper_model": "base"}), encoding="utf-8"
            )
            lectures_dir = source_dir / "lectures" / "lecture-1"
            lectures_dir.mkdir(parents=True)
            (lectures_dir / "lesson.md").write_text("# Lesson Notes", encoding="utf-8")

            dest_paths = PlatformAppPaths(test_root=Path(temp_dir) / "app")
            result = migrate_legacy_data(dest_paths, legacy_source=source_dir)

            self.assertEqual(result.status, MigrationStatus.COMPLETED)
            self.assertEqual(result.copied_files_count, 3)

            # Destination has the files
            self.assertTrue((dest_paths.data_dir / "library.json").is_file())
            self.assertTrue((dest_paths.data_dir / "settings.json").is_file())
            self.assertTrue(
                (dest_paths.data_dir / "lectures" / "lecture-1" / "lesson.md").is_file()
            )

            # Source still has the original files (not deleted)
            self.assertTrue((source_dir / "library.json").is_file())

            # Receipt exists and is valid
            receipt_path = dest_paths.data_dir / "migration-receipt.json"
            self.assertTrue(receipt_path.is_file())
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["file_count"], 3)
            self.assertIn("library.json", receipt_data["files"])

            # Staging directory is cleaned up
            self.assertFalse((dest_paths.data_dir / ".migration-staging").exists())

    def test_repeat_run_returns_already_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "legacy"
            source_dir.mkdir()
            (source_dir / "library.json").write_text("[]", encoding="utf-8")

            dest_paths = PlatformAppPaths(test_root=Path(temp_dir) / "app")
            first = migrate_legacy_data(dest_paths, legacy_source=source_dir)
            self.assertEqual(first.status, MigrationStatus.COMPLETED)

            second = migrate_legacy_data(dest_paths, legacy_source=source_dir)
            self.assertEqual(second.status, MigrationStatus.ALREADY_MIGRATED)

    def test_conflict_detection_when_libraries_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "legacy"
            source_dir.mkdir()
            (source_dir / "library.json").write_text(
                json.dumps([{"id": "source"}]), encoding="utf-8"
            )

            dest_paths = PlatformAppPaths(test_root=Path(temp_dir) / "app")
            dest_paths.data_dir.mkdir(parents=True)
            (dest_paths.data_dir / "library.json").write_text(
                json.dumps([{"id": "dest"}]), encoding="utf-8"
            )

            result = migrate_legacy_data(dest_paths, legacy_source=source_dir)

            self.assertEqual(result.status, MigrationStatus.CONFLICT)
            self.assertIn("конфликт", result.error_message or "")

            # Verify neither library was modified
            self.assertEqual(
                json.loads((dest_paths.data_dir / "library.json").read_text(encoding="utf-8")),
                [{"id": "dest"}],
            )
            self.assertEqual(
                json.loads((source_dir / "library.json").read_text(encoding="utf-8")),
                [{"id": "source"}],
            )

    def test_hash_mismatch_aborts_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = Path(temp_dir) / "legacy"
            source_dir.mkdir()
            (source_dir / "library.json").write_text("[]", encoding="utf-8")

            dest_paths = PlatformAppPaths(test_root=Path(temp_dir) / "app")

            # Corrupt calculate_sha256 during staging check
            call_count = 0

            def fake_calculate(path: Path) -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    return "mismatched_hash"
                return "matching_hash"

            with patch(
                "konspekt.platform_services.migration.calculate_sha256", side_effect=fake_calculate
            ):
                result = migrate_legacy_data(dest_paths, legacy_source=source_dir)

            self.assertEqual(result.status, MigrationStatus.ERROR)
            self.assertFalse((dest_paths.data_dir / ".migration-staging").exists())
            self.assertFalse((dest_paths.data_dir / "library.json").exists())


if __name__ == "__main__":
    unittest.main()
