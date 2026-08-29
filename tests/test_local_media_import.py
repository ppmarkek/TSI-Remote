from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.library_manager import load_library
from konspekt.local_media_import import (
    LocalMediaImportError,
    import_local_media_file,
)


class LocalMediaImportTests(unittest.TestCase):
    def test_import_local_audio_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "lecture_recording.mp3"
            source_file.write_bytes(b"dummy audio data stream for testing")
            lib_path = temp_path / "library.json"

            recording, lec_dir = import_local_media_file(
                source_file,
                custom_title="Custom Local Lecture",
                library_path=lib_path,
            )

            self.assertTrue(recording.meeting_id.startswith("local-"))
            self.assertEqual(recording.title, "Custom Local Lecture")
            self.assertTrue(recording.source_url.startswith("local://media-"))
            self.assertEqual(recording.source_url, f"local://media-{recording.meeting_id[6:]}")

            # Check files and manifest in directory
            self.assertTrue((lec_dir / "audio.mp4").is_file())
            self.assertTrue((lec_dir / "lecture-manifest.json").is_file())

            # Check library entry
            recordings = load_library(lib_path)
            self.assertEqual(len(recordings), 1)
            self.assertEqual(recordings[0].title, "Custom Local Lecture")

    def test_rejects_unsupported_file_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bad_file = temp_path / "document.pdf"
            bad_file.write_bytes(b"pdf data")

            with self.assertRaises(LocalMediaImportError):
                import_local_media_file(bad_file)


if __name__ == "__main__":
    unittest.main()
