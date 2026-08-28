from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.bbb_import import load_library
from konspekt.workflow import resolve_lecture_state


class LibraryPerformanceTests(unittest.TestCase):
    def test_library_is_loaded_once_for_hundred_recordings(self) -> None:
        recordings_data = [
            {
                "meeting_id": f"m-{i}",
                "source_url": f"https://bbb.test/p?m={i}",
                "title": f"Lecture {i}",
                "imported_at": "2026-08-01T10:00:00+00:00",
                "audio_video_url": f"https://bbb.test/a-{i}.mp4",
                "screen_video_url": None,
                "slides": [],
            }
            for i in range(100)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            lib_path = Path(temp_dir) / "library.json"
            lib_path.write_text(json.dumps(recordings_data), encoding="utf-8")

            read_count = 0
            original_read_text = Path.read_text

            def counting_read_text(self_path: Path, *args: object, **kwargs: object) -> str:
                nonlocal read_count
                if self_path.name == "library.json":
                    read_count += 1
                return original_read_text(self_path, *args, **kwargs)

            with patch.object(Path, "read_text", side_effect=counting_read_text, autospec=True):
                # Load library once
                recordings = load_library(lib_path)
                self.assertEqual(len(recordings), 100)

                # Process 100 recordings in memory without re-reading library.json
                states = [
                    resolve_lecture_state(Path(temp_dir) / rec.meeting_id) for rec in recordings
                ]
                self.assertEqual(len(states), 100)

            # Assert library.json was read exactly once, NOT 100 times
            self.assertEqual(read_count, 1)


if __name__ == "__main__":
    unittest.main()
