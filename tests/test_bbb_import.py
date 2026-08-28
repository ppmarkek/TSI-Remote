from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.bbb_import import (
    BBBImportError,
    BBBRecording,
    inspect_bbb_recording,
    load_library,
    load_library_with_quarantine,
    save_to_library,
)

MEETING_ID = "f0a35ad2f6165a2fbce2f5d9e6ca241673f63bf8-1758353019485"
PLAYBACK_URL = (
    f"https://bbb-lb.tsi.lv/playback/presentation/2.0/playback.html?meetingId={MEETING_ID}"
)


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, metadata: dict[str, str]) -> None:
        self.metadata = metadata

    def head(self, url: str, **_: object) -> FakeResponse:
        return FakeResponse(200 if url.endswith(("webcams.mp4", "deskshare.mp4")) else 404)

    def get(self, url: str, **_: object) -> FakeResponse:
        for name, body in self.metadata.items():
            if url.endswith(name):
                return FakeResponse(text=body)
        return FakeResponse(404)


class BBBImportTests(unittest.TestCase):
    def test_rejects_path_traversal_in_meeting_id(self) -> None:
        url = (
            "https://bbb.example.test/playback/presentation/2.0/playback.html"
            "?meetingId=..%2F..%2Foutside"
        )

        with self.assertRaises(BBBImportError):
            inspect_bbb_recording(url, session=FakeSession({}))

    def test_inspects_media_title_and_slide_text(self) -> None:
        session = FakeSession(
            {
                "metadata.xml": '<recording><meeting name="Databases 101" /></recording>',
                "presentation_text.json": json.dumps(
                    {"deck": {"slide-1": "Primary keys", "slide-2": ""}}
                ),
                "slides_new.xml": "<popcorn />",
            }
        )

        recording = inspect_bbb_recording(PLAYBACK_URL, session=session)

        self.assertEqual(recording.title, "Databases 101")
        self.assertTrue(recording.audio_video_url.endswith("video/webcams.mp4"))
        self.assertTrue(recording.screen_video_url.endswith("deskshare/deskshare.mp4"))
        self.assertEqual([slide.identifier for slide in recording.slides], ["slide-1", "slide-2"])
        self.assertTrue(recording.has_slide_text)

    def test_saves_one_recording_per_meeting(self) -> None:
        session = FakeSession(
            {
                "metadata.xml": '<recording><meeting name="Databases 101" /></recording>',
                "presentation_text.json": "{}",
                "slides_new.xml": "<popcorn />",
            }
        )
        recording = inspect_bbb_recording(PLAYBACK_URL, session=session)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            save_to_library(recording, path)
            save_to_library(recording, path)

            loaded = load_library(path)

        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], BBBRecording)

    def test_keeps_same_meeting_id_from_different_bbb_hosts(self) -> None:
        first = inspect_bbb_recording(
            PLAYBACK_URL,
            session=FakeSession(
                {
                    "metadata.xml": '<recording><meeting name="First host" /></recording>',
                    "presentation_text.json": "{}",
                    "slides_new.xml": "<popcorn />",
                }
            ),
        )
        second = BBBRecording(
            meeting_id=first.meeting_id,
            source_url=first.source_url.replace("bbb-lb.tsi.lv", "bbb.other.test"),
            title="Second host",
            imported_at="2026-07-15T11:00:00+00:00",
            audio_video_url=first.audio_video_url.replace("bbb-lb.tsi.lv", "bbb.other.test"),
            screen_video_url=None,
            slides=(),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            save_to_library(first, path)
            save_to_library(second, path)
            loaded = load_library(path)

        self.assertEqual(len(loaded), 2)
        self.assertEqual({item.title for item in loaded}, {"First host", "Second host"})

    def test_quarantines_malformed_row_and_preserves_valid_rows(self) -> None:
        valid_recording = {
            "meeting_id": "valid-meeting-1",
            "source_url": "https://bbb.example.test/valid",
            "title": "Valid Lecture",
            "imported_at": "2026-08-01T12:00:00+00:00",
            "audio_video_url": "https://bbb.example.test/video.mp4",
            "screen_video_url": None,
            "slides": [],
        }
        corrupted_row = {"unexpected": "payload", "broken": True}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            path.write_text(
                json.dumps({"schema_version": 1, "recordings": [valid_recording, corrupted_row]}),
                encoding="utf-8",
            )

            loaded, quarantine = load_library_with_quarantine(path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].meeting_id, "valid-meeting-1")
            self.assertIsNotNone(quarantine)
            self.assertTrue(quarantine.is_file())
            self.assertIn("library-corrupt-", quarantine.name)

            # Check that re-reading the library file now contains only the valid sanitized row
            reloaded = load_library(path)
            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].meeting_id, "valid-meeting-1")

    def test_handles_unknown_version_forward_compatibility(self) -> None:
        valid_recording = {
            "meeting_id": "future-meeting",
            "source_url": "https://bbb.example.test/future",
            "title": "Future Lecture",
            "imported_at": "2026-08-01T12:00:00+00:00",
            "audio_video_url": "https://bbb.example.test/video.mp4",
            "screen_video_url": None,
            "slides": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            path.write_text(
                json.dumps({"schema_version": 99, "recordings": [valid_recording]}),
                encoding="utf-8",
            )

            loaded = load_library(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].meeting_id, "future-meeting")

    def test_quarantines_corrupted_json_and_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            path.write_text(
                '{"schema_version": 1, "recordings": [{"meeting_id": "truncated', encoding="utf-8"
            )

            with self.assertRaises(BBBImportError):
                load_library(path)

            corrupt_backups = list(Path(directory).glob("library-corrupt-*.json"))
            self.assertEqual(len(corrupt_backups), 1)
            self.assertIn("truncated", corrupt_backups[0].read_text(encoding="utf-8"))

    def test_read_only_directory_quarantine_fallback(self) -> None:
        valid_recording = {
            "meeting_id": "valid-m",
            "source_url": "https://bbb.example.test/v",
            "title": "Valid",
            "imported_at": "2026-08-01T12:00:00+00:00",
            "audio_video_url": "https://bbb.example.test/v.mp4",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            path.write_text(
                json.dumps([valid_recording, {"corrupt": True}]),
                encoding="utf-8",
            )

            # Mock quarantine write to fail with PermissionError
            with patch("konspekt.bbb_import._quarantine_backup", return_value=None):
                loaded = load_library(path)
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].meeting_id, "valid-m")


if __name__ == "__main__":
    unittest.main()
