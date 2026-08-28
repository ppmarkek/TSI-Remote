from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.bbb_import import BBBRecording
from konspekt.lesson_output import (
    LessonOutputError,
    read_generated_lesson,
    save_generated_lesson,
)


class LessonOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="meeting-lesson",
            source_url="https://example.test/playback?meetingId=meeting-lesson",
            title="Generated lesson",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

    def test_saves_and_reads_user_reviewed_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            saved = save_generated_lesson(
                self.recording,
                "# Lesson\r\n\r\nUseful content",
                directory=directory,
            )

            content = read_generated_lesson(self.recording, directory=directory)

        self.assertEqual(saved.path.name, "lesson.md")
        self.assertEqual(saved.character_count, len("# Lesson\n\nUseful content"))
        self.assertEqual(content, "# Lesson\n\nUseful content")

    def test_rejects_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(LessonOutputError):
                save_generated_lesson(self.recording, "  \n", directory=Path(temporary))

    def test_reports_missing_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(LessonOutputError):
                read_generated_lesson(self.recording, directory=Path(temporary))

    def test_error_mid_write_preserves_old_lesson_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            # Write initial lesson
            save_generated_lesson(self.recording, "# Old Lesson Content", directory=directory)
            self.assertEqual(
                read_generated_lesson(self.recording, directory=directory), "# Old Lesson Content"
            )

            # Simulate failure during os.replace
            with patch("os.replace", side_effect=OSError("Disk write failure")):
                with self.assertRaises(LessonOutputError):
                    save_generated_lesson(
                        self.recording, "# New Corrupted Content", directory=directory
                    )

            # Check that old content was preserved
            self.assertEqual(
                read_generated_lesson(self.recording, directory=directory), "# Old Lesson Content"
            )

            # Check that temp files are cleaned up
            temp_files = list(directory.glob("*.tmp"))
            self.assertEqual(temp_files, [])

    def test_successful_write_atomically_replaces_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            save_generated_lesson(self.recording, "# Lesson Version 1", directory=directory)
            self.assertEqual(
                read_generated_lesson(self.recording, directory=directory), "# Lesson Version 1"
            )

            save_generated_lesson(self.recording, "# Lesson Version 2", directory=directory)
            self.assertEqual(
                read_generated_lesson(self.recording, directory=directory), "# Lesson Version 2"
            )

            # Check that no temporary files remain
            temp_files = list(directory.glob("*.tmp")) + list(directory.glob(".*.tmp"))
            self.assertEqual(temp_files, [])


if __name__ == "__main__":
    unittest.main()
