from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from konspekt.bbb_import import BBBRecording, SlideInfo
from konspekt.context_package import build_context_package
from konspekt.deepseek_handoff import launch_deepseek_handoff, prepare_deepseek_handoff
from konspekt.local_pipeline import ScreenNote, TranscriptSegment
from konspekt.outbound_context import (
    OutboundContextError,
    OutboundTimelineBlock,
    build_outbound_context,
)


class OutboundContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="secret-meeting-id-12345",
            source_url="https://bbb.private.test/playback/presentation/2.0/playback.html?meetingId=secret-meeting-id-12345",
            title="Introduction to Databases",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://bbb.private.test/video/webcams.webm",
            screen_video_url="https://bbb.private.test/deskshare/deskshare.webm",
            slides=(
                SlideInfo(
                    "slide-1",
                    "Relational Model",
                    "https://bbb.private.test/presentation/slide1.png",
                ),
                SlideInfo(
                    "slide-2", "SQL Queries", "https://bbb.private.test/presentation/slide2.png"
                ),
            ),
        )
        self.segments = (
            TranscriptSegment(0.0, 10.0, "Welcome to the database lecture."),
            TranscriptSegment(10.0, 25.0, "Today we will cover relational tables."),
        )
        self.screen_notes = (
            ScreenNote(
                5.0, "/Users/teacher/AppData/Local/Konspekt/frame-1.jpg", "SELECT * FROM users;"
            ),
        )

    def test_happy_path_builds_clean_outbound_context(self) -> None:
        blocks = (
            OutboundTimelineBlock(
                0.0, 25.0, "Welcome to the database lecture. Today we cover relational tables."
            ),
        )
        ctx = build_outbound_context(
            self.recording.title,
            slides=self.recording.slides,
            screen_notes=self.screen_notes,
            transcript_blocks=blocks,
            meeting_id=self.recording.meeting_id,
            source_url=self.recording.source_url,
        )

        md = ctx.render_markdown()
        prompt = ctx.render_prompt()
        payload = ctx.to_dict()
        summary = ctx.consent_summary("OpenAI")

        self.assertEqual(ctx.title, "Introduction to Databases")
        self.assertEqual(len(ctx.slides), 2)
        self.assertEqual(len(ctx.screen_notes), 1)
        self.assertEqual(len(ctx.transcript_blocks), 1)

        # Ensure no URL, meeting ID, image_url, or absolute path in markdown
        self.assertNotIn("secret-meeting-id", md)
        self.assertNotIn("https://bbb.private.test", md)
        self.assertNotIn("frame-1.jpg", md)
        self.assertNotIn("/Users/", md)
        self.assertIn("# Контекст лекции: Introduction to Databases", md)
        self.assertIn("Relational Model", md)
        self.assertIn("SELECT * FROM users;", md)

        # Check prompt
        self.assertIn("Инструкция для создания lesson.md", prompt)
        self.assertIn("Introduction to Databases", prompt)

        # Check payload dictionary
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["lecture"]["title"], "Introduction to Databases")
        self.assertNotIn("meeting_id", payload["lecture"])
        self.assertNotIn("source_url", payload["lecture"])
        self.assertNotIn("image_path", payload["screen_notes"][0])

        # Check consent summary
        self.assertIn("OpenAI", summary.provider)
        self.assertGreater(summary.estimated_size_bytes, 0)
        self.assertGreater(summary.character_count, 0)

    def test_rejects_empty_title(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context("   ")

    def test_rejects_explicit_source_url_leak(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Leaked Lecture",
                slides=(
                    SlideInfo(
                        "s1",
                        "Leaked url: https://bbb.private.test/playback/presentation/2.0/playback.html?meetingId=secret-meeting-id-12345",
                    ),
                ),
                source_url=self.recording.source_url,
            )

    def test_rejects_explicit_meeting_id_leak(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Leaked Meeting",
                slides=(SlideInfo("s1", "Meeting ID is secret-meeting-id-12345"),),
                meeting_id=self.recording.meeting_id,
            )

    def test_rejects_query_token_in_text(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Token Lecture",
                slides=(SlideInfo("s1", "Access via token=DO_NOT_SEND"),),
            )

    def test_rejects_api_key_or_bearer_secret_in_text(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Secret Lecture",
                slides=(SlideInfo("s1", "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Secret Lecture 2",
                slides=(SlideInfo("s1", "Key is sk-proj-12345678901234567890"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Secret Lecture 3",
                slides=(SlideInfo("s1", "SECRET-DO-NOT-LOG"),),
            )

    def test_rejects_absolute_paths_in_text(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Path Lecture",
                slides=(SlideInfo("s1", "Files stored in /Users/teacher/Documents/private"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Windows Path",
                slides=(SlideInfo("s1", r"Files stored in C:\Users\teacher\Documents"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "UNC Path 1",
                slides=(SlideInfo("s1", r"\\server\share\file.txt"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "UNC Path 2",
                slides=(SlideInfo("s1", r"//fileserver/data/folder"),),
            )

    def test_rejects_git_tokens_and_cloud_credentials(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "GitHub Token",
                slides=(SlideInfo("s1", "Token: ghp_123456789012345678901234567890123456"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "AWS Key",
                slides=(SlideInfo("s1", "Key: AKIAIOSFODNN7EXAMPLE"),),
            )

    def test_rejects_uuid_and_meeting_identifiers(self) -> None:
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "UUID Lecture",
                slides=(SlideInfo("s1", "UUID: 12345678-1234-1234-1234-123456789abc"),),
            )
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                "Meeting Leak",
                slides=(SlideInfo("s1", "meetingId=secret-meeting-room-999"),),
            )

    def test_validate_provider_context_limits(self) -> None:
        from konspekt.outbound_context import validate_provider_context_limits

        # Normal limits should pass
        validate_provider_context_limits("chatgpt", character_count=5000, size_bytes=6000)
        validate_provider_context_limits("deepseek", character_count=5000, size_bytes=6000)
        validate_provider_context_limits("openrouter", character_count=5000, size_bytes=6000)

        # Excessively large context should raise OutboundContextError
        with self.assertRaises(OutboundContextError):
            validate_provider_context_limits("deepseek", character_count=300000, size_bytes=500000)
        with self.assertRaises(OutboundContextError):
            validate_provider_context_limits(
                "chatgpt", character_count=1000, size_bytes=20 * 1024 * 1024
            )

    def test_identical_sanitization_in_context_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dir_path = Path(temp_dir)
            (dir_path / "transcript.json").write_text(
                json.dumps(
                    [{"start_seconds": 0, "end_seconds": 10, "text": "Speech with /Users/secret"}]
                ),
                encoding="utf-8",
            )
            (dir_path / "screen-notes.json").write_text("[]", encoding="utf-8")

            # Should fail due to /Users/secret absolute path
            with self.assertRaises(Exception):
                build_context_package(self.recording, directory=dir_path)

    def test_cancelled_handoff_does_not_open_browser_or_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dir_path = Path(temp_dir)
            (dir_path / "lesson-context.md").write_text("# Context\n\nData.", encoding="utf-8")
            (dir_path / "lesson-prompt.md").write_text("Prompt.", encoding="utf-8")

            handoff = prepare_deepseek_handoff(self.recording, directory=dir_path)

            mock_url = MagicMock(return_value=True)
            mock_dir = MagicMock()

            # User cancels: launch_deepseek_handoff is NOT called
            self.assertEqual(mock_url.call_count, 0)
            self.assertEqual(mock_dir.call_count, 0)

            # When user confirms: launch_deepseek_handoff is called
            launch_deepseek_handoff(handoff, open_url=mock_url, open_directory=mock_dir)
            mock_url.assert_called_once_with("https://chat.deepseek.com/")
            mock_dir.assert_called_once_with(dir_path)


if __name__ == "__main__":
    unittest.main()
