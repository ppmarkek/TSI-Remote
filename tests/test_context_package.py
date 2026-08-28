from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from konspekt.bbb_import import BBBRecording, SlideInfo
from konspekt.context_package import ContextPackageError, build_context_package


class ContextPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="meeting-context",
            source_url="https://example.test/playback?meetingId=meeting-context",
            title="Context lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(
                SlideInfo("slide-1", " First key idea "),
                SlideInfo("slide-2", "First   key idea"),
                SlideInfo("slide-3", "Second key idea"),
            ),
        )

    def test_builds_attachable_context_and_prompt_without_llm(self) -> None:
        progress_updates: list[tuple[int, str]] = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "transcript.json").write_text(
                json.dumps(
                    [
                        {"start_seconds": 1, "end_seconds": 6, "text": "First explanation."},
                        {"start_seconds": 12, "end_seconds": 20, "text": "Second explanation."},
                    ]
                ),
                encoding="utf-8",
            )
            (directory / "screen-notes.json").write_text(
                json.dumps(
                    [
                        {"timestamp_seconds": 0, "image_path": "frame-1.jpg", "text": "Diagram A"},
                        {"timestamp_seconds": 30, "image_path": "frame-2.jpg", "text": "Diagram A"},
                        {"timestamp_seconds": 60, "image_path": "frame-3.jpg", "text": "Formula B"},
                    ]
                ),
                encoding="utf-8",
            )

            package = build_context_package(
                self.recording,
                directory=directory,
                progress=lambda percent, message: progress_updates.append((percent, message)),
            )
            markdown = package.markdown_path.read_text(encoding="utf-8")
            prompt = package.prompt_path.read_text(encoding="utf-8")
            payload = json.loads(package.json_path.read_text(encoding="utf-8"))

        self.assertEqual(package.timeline_block_count, 1)
        self.assertEqual(package.slide_count, 2)
        self.assertEqual(package.screen_note_count, 2)
        self.assertIn("lesson.md", prompt)
        self.assertIn("00:00:01 — 00:00:20", markdown)
        self.assertIn("First explanation. Second explanation.", markdown)
        self.assertEqual(payload["lecture"]["title"], "Context lecture")
        self.assertNotIn("meeting_id", payload["lecture"])
        self.assertNotIn("source_url", payload["lecture"])
        self.assertNotIn("meeting-context", markdown)
        self.assertEqual(progress_updates[-1][0], 100)

    def test_requires_a_prepared_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ContextPackageError):
                build_context_package(self.recording, directory=Path(temporary))


if __name__ == "__main__":
    unittest.main()
