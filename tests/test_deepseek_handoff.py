from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.bbb_import import BBBRecording
from konspekt.deepseek_handoff import (
    DEEPSEEK_URL,
    DeepSeekHandoffError,
    launch_deepseek_handoff,
    prepare_deepseek_handoff,
)


class DeepSeekHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="meeting-deepseek",
            source_url="https://example.test/playback?meetingId=meeting-deepseek",
            title="DeepSeek lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

    def test_prepares_local_checklist_and_opens_only_user_chosen_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-context.md").write_text("lecture context", encoding="utf-8")
            (directory / "lesson-prompt.md").write_text("make lesson.md", encoding="utf-8")

            handoff = prepare_deepseek_handoff(self.recording, directory=directory)
            opened_urls: list[str] = []
            opened_directories: list[Path] = []
            launch_deepseek_handoff(
                handoff,
                open_url=lambda url: opened_urls.append(url) or True,
                open_directory=opened_directories.append,
            )

            instructions = handoff.instructions_path.read_text(encoding="utf-8")

        self.assertEqual(opened_urls, [DEEPSEEK_URL])
        self.assertEqual(opened_directories, [directory])
        self.assertIn("Не включай веб-поиск", instructions)

    def test_requires_context_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DeepSeekHandoffError):
                prepare_deepseek_handoff(self.recording, directory=Path(temporary))

    def test_prepare_handoff_rejects_sensitive_path_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-context.md").write_text(
                "Leaked /Users/private/data.txt", encoding="utf-8"
            )
            (directory / "lesson-prompt.md").write_text("Prompt", encoding="utf-8")

            with self.assertRaises(DeepSeekHandoffError):
                prepare_deepseek_handoff(self.recording, directory=directory)

    def test_launch_handoff_validates_outbound_context_immediately_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            context_file = directory / "lesson-context.md"
            prompt_file = directory / "lesson-prompt.md"
            context_file.write_text("Clean context", encoding="utf-8")
            prompt_file.write_text("Clean prompt", encoding="utf-8")

            handoff = prepare_deepseek_handoff(self.recording, directory=directory)

            # Alter file after prepare to inject secret
            context_file.write_text("Secret: ghp_123456789012345678901234567890", encoding="utf-8")

            opened_urls: list[str] = []
            with self.assertRaises(DeepSeekHandoffError):
                launch_deepseek_handoff(
                    handoff,
                    open_url=lambda url: opened_urls.append(url) or True,
                    recording=self.recording,
                )
            self.assertEqual(opened_urls, [])


if __name__ == "__main__":
    unittest.main()
