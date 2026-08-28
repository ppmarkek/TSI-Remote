from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from konspekt.bbb_import import BBBRecording, load_library, save_library
from konspekt.library_manager import (
    export_lecture_archive,
    filter_and_sort_recordings,
    move_to_trash,
    rename_recording,
    restore_from_trash,
)


class LibraryActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recordings = [
            BBBRecording(
                meeting_id="m-1",
                source_url="https://bbb.test/p?m=1",
                title="Algorithms and Data Structures",
                imported_at="2026-08-01T10:00:00+00:00",
                audio_video_url="https://bbb.test/a1.mp4",
                screen_video_url=None,
                slides=[],
            ),
            BBBRecording(
                meeting_id="m-2",
                source_url="https://bbb.test/p?m=2",
                title="Database Architecture",
                imported_at="2026-08-02T10:00:00+00:00",
                audio_video_url="https://bbb.test/a2.mp4",
                screen_video_url=None,
                slides=[],
            ),
        ]

    def test_filter_and_sort_recordings(self) -> None:
        # Search query filter
        filtered = filter_and_sort_recordings(self.recordings, query="data")
        self.assertEqual(len(filtered), 2)  # Algorithms and Data Structures, Database Architecture

        filtered_algo = filter_and_sort_recordings(self.recordings, query="algo")
        self.assertEqual(len(filtered_algo), 1)
        self.assertEqual(filtered_algo[0].meeting_id, "m-1")

        # Sort by title
        sorted_title = filter_and_sort_recordings(self.recordings, sort_by="title_desc")
        self.assertEqual(sorted_title[0].meeting_id, "m-2")

        # Sort by date
        sorted_date = filter_and_sort_recordings(self.recordings, sort_by="date_asc")
        self.assertEqual(sorted_date[0].meeting_id, "m-1")

    def test_rename_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lib_path = Path(temp_dir) / "library.json"
            save_library(self.recordings, path=lib_path)

            updated = rename_recording(lib_path, "m-1", "Advanced Algorithms")
            target = next(r for r in updated if r.meeting_id == "m-1")
            self.assertEqual(target.title, "Advanced Algorithms")

            with self.assertRaises(ValueError):
                rename_recording(lib_path, "m-1", "   ")

    def test_export_clean_archive_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            lec_dir = base_dir / "m-1"
            lec_dir.mkdir()
            (lec_dir / "lesson.md").write_text("# Lesson Notes", encoding="utf-8")
            (lec_dir / "lesson-context.md").write_text("# Context", encoding="utf-8")
            (lec_dir / "lecture-manifest.json").write_text(
                '{"secret": "do-not-export"}', encoding="utf-8"
            )

            zip_dest = base_dir / "exports" / "m-1-export.zip"
            export_lecture_archive(lec_dir, zip_dest)

            self.assertTrue(zip_dest.is_file())
            with zipfile.ZipFile(zip_dest, "r") as archive:
                names = archive.namelist()
                self.assertIn("lesson.md", names)
                self.assertIn("lesson-context.md", names)
                self.assertNotIn("lecture-manifest.json", names)

    def test_move_to_trash_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            lib_path = base_dir / "library.json"
            save_library(self.recordings, path=lib_path)

            lec_dir = base_dir / "m-1"
            lec_dir.mkdir()
            (lec_dir / "lesson.md").write_text("# Lesson m-1", encoding="utf-8")

            # Move to trash
            move_to_trash(lib_path, "m-1", base_dir)
            self.assertFalse(lec_dir.exists())
            self.assertTrue((base_dir / "trash" / "m-1").exists())

            # Restore from trash
            restore_from_trash(lib_path, "m-1", base_dir)
            self.assertTrue(lec_dir.exists())
            self.assertTrue((lec_dir / "lesson.md").is_file())

    def test_move_to_trash_preserves_distinct_origins_with_same_meeting_id(self) -> None:
        first = BBBRecording(
            meeting_id="shared-id",
            source_url="https://host-a.test/p?m=shared-id",
            title="Lecture from Host A",
            imported_at="2026-08-01T10:00:00+00:00",
            audio_video_url="https://host-a.test/a.mp4",
            screen_video_url=None,
            slides=[],
        )
        second = BBBRecording(
            meeting_id="shared-id",
            source_url="https://host-b.test/p?m=shared-id",
            title="Lecture from Host B",
            imported_at="2026-08-01T11:00:00+00:00",
            audio_video_url="https://host-b.test/b.mp4",
            screen_video_url=None,
            slides=[],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            lib_path = base_dir / "library.json"
            save_library([first, second], path=lib_path)

            from konspekt.local_pipeline import default_lecture_directory

            dir_a = default_lecture_directory(first, base_dir=base_dir)
            dir_b = default_lecture_directory(second, base_dir=base_dir)
            dir_a.mkdir(parents=True)
            dir_b.mkdir(parents=True)
            (dir_a / "lesson.md").write_text("# Host A Lesson", encoding="utf-8")
            (dir_b / "lesson.md").write_text("# Host B Lesson", encoding="utf-8")

            # Move ONLY host A to trash
            move_to_trash(lib_path, "shared-id", base_dir, source_url=first.source_url)

            # Library must still have host B
            active = load_library(lib_path)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].title, "Lecture from Host B")
            self.assertTrue(dir_b.exists())
            self.assertFalse(dir_a.exists())

            # Restore host A from trash
            restore_from_trash(lib_path, "shared-id", base_dir, source_url=first.source_url)
            restored_lib = load_library(lib_path)
            self.assertEqual(len(restored_lib), 2)
            self.assertTrue(dir_a.exists())


if __name__ == "__main__":
    unittest.main()
