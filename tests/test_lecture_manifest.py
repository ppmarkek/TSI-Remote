from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.lecture_manifest import (
    ALGORITHM_VERSION,
    LectureManifest,
    ManifestError,
    compute_lecture_id,
    file_sha256,
)


class LectureManifestTests(unittest.TestCase):
    def test_stable_lecture_id_generation(self) -> None:
        url_1 = (
            "https://bbb-lb.tsi.lv/playback/presentation/2.0/playback.html?meetingId=meeting-100"
        )
        url_2 = "https://bbb.other-domain.test/playback/presentation/2.0/playback.html?meetingId=meeting-100"
        meeting_id = "meeting-100"

        id_1a = compute_lecture_id(url_1, meeting_id)
        id_1b = compute_lecture_id(url_1, meeting_id)
        id_2 = compute_lecture_id(url_2, meeting_id)

        # Same origin + meeting ID -> identical lecture ID
        self.assertEqual(id_1a, id_1b)
        # Different origin with same meeting ID -> different lecture IDs
        self.assertNotEqual(id_1a, id_2)
        self.assertIn("meeting-100", id_1a)
        self.assertIn("meeting-100", id_2)

    def test_stage_fingerprint_validation_and_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            transcript_file = base_dir / "transcript.json"
            transcript_file.write_text('{"text": "Hello world"}', encoding="utf-8")
            transcript_hash = file_sha256(transcript_file)

            manifest = LectureManifest(
                lecture_id="test-lecture",
                meeting_id="m-1",
                source_url="https://bbb.test/p?m=1",
            )

            fingerprint_base = {
                "whisper_model": "base",
                "frame_interval_seconds": 60,
                "algorithm_version": ALGORITHM_VERSION,
            }
            manifest.record_stage_success(
                "transcription",
                fingerprint=fingerprint_base,
                outputs={"transcript.json": transcript_hash},
            )

            # Valid with matching fingerprint and intact file
            self.assertTrue(manifest.is_stage_valid("transcription", fingerprint_base, base_dir))

            # Invalid if whisper model changed to "small"
            fingerprint_small = {
                "whisper_model": "small",
                "frame_interval_seconds": 60,
                "algorithm_version": ALGORITHM_VERSION,
            }
            self.assertFalse(manifest.is_stage_valid("transcription", fingerprint_small, base_dir))

            # Invalid if output file content was modified
            transcript_file.write_text('{"text": "Modified content"}', encoding="utf-8")
            self.assertFalse(manifest.is_stage_valid("transcription", fingerprint_base, base_dir))

            # Invalid if output file deleted
            transcript_file.unlink()
            self.assertFalse(manifest.is_stage_valid("transcription", fingerprint_base, base_dir))

    def test_save_and_load_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "lecture-manifest.json"
            manifest = LectureManifest(
                lecture_id="lec-42",
                meeting_id="meeting-42",
                source_url="https://bbb.test/m=42",
            )
            manifest.record_stage_success(
                "download",
                fingerprint={"audio_url": "https://bbb.test/a.mp4"},
                outputs={"audio.mp4": "sha256dummy"},
            )
            manifest.save(path)

            loaded = LectureManifest.load(path)
            self.assertEqual(loaded.lecture_id, "lec-42")
            self.assertEqual(loaded.meeting_id, "meeting-42")
            self.assertEqual(loaded.stages["download"].status, "completed")
            self.assertEqual(loaded.stages["download"].outputs["audio.mp4"], "sha256dummy")

    def test_load_corrupted_manifest_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "lecture-manifest.json"
            path.write_text("corrupted json {", encoding="utf-8")
            with self.assertRaises(ManifestError):
                LectureManifest.load(path)


if __name__ == "__main__":
    unittest.main()
