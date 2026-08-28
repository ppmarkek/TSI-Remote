from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.bbb_import import BBBRecording, save_library
from konspekt.context_package import build_context_package
from konspekt.lesson_output import save_lesson_markdown
from konspekt.outbound_context import build_outbound_context
from konspekt.workflow import (
    LectureState,
    next_action,
    resolve_lecture_state,
)


class EndToEndWorkflowTests(unittest.TestCase):
    def test_full_pipeline_from_import_to_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            meeting_id = "test-session-101"
            lec_dir = base_dir / meeting_id
            lec_dir.mkdir(parents=True)

            recording = BBBRecording(
                meeting_id=meeting_id,
                source_url="https://bbb.test/p?meetingId=test-session-101",
                title="Introduction to Algorithms",
                imported_at="2026-08-01T12:00:00+00:00",
                audio_video_url="https://bbb.test/audio.mp4",
                screen_video_url=None,
                slides=[],
            )
            save_library([recording], path=base_dir / "library.json")

            # 1. Initially imported
            state = resolve_lecture_state(lec_dir)
            self.assertEqual(state, LectureState.IMPORTED)
            action = next_action(state)
            self.assertEqual(action.action_type, "prepare_local")

            # 2. Local preparation completes (transcript written)
            (lec_dir / "transcript.json").write_text(
                '[{"text": "Algorithm complexity O(N)"}]', encoding="utf-8"
            )
            state = resolve_lecture_state(lec_dir)
            self.assertEqual(state, LectureState.PREPARED)
            action = next_action(state)
            self.assertEqual(action.action_type, "build_package")

            # 3. Context package built
            package = build_context_package(recording, directory=lec_dir)
            self.assertTrue(package.markdown_path.is_file())
            self.assertTrue(package.prompt_path.is_file())

            state = resolve_lecture_state(lec_dir)
            self.assertEqual(state, LectureState.PACKAGE_READY)
            action = next_action(state)
            self.assertEqual(action.action_type, "request_consent")

            # 4. Outbound context consent verified
            outbound = build_outbound_context(
                title=recording.title,
                transcript_text="Algorithm complexity O(N)",
                slides_text="",
                ocr_notes_text="",
            )
            self.assertNotIn("https://", outbound.sanitized_text)
            self.assertNotIn(meeting_id, outbound.sanitized_text)

            # 5. Lesson generated and saved
            lesson_md = "# Lesson: Introduction to Algorithms\n\n## Key concepts\n- Big O notation"
            save_lesson_markdown(lec_dir, lesson_md)

            state = resolve_lecture_state(lec_dir)
            self.assertEqual(state, LectureState.LESSON_READY)
            action = next_action(state)
            self.assertEqual(action.action_type, "view_lesson")


if __name__ == "__main__":
    unittest.main()
