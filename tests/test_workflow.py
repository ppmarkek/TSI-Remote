from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.workflow import (
    LectureState,
    WorkflowCapabilities,
    next_action,
    resolve_lecture_state,
)


class WorkflowTests(unittest.TestCase):
    def test_next_action_state_table(self) -> None:
        caps = WorkflowCapabilities(api_configured=True, chatgpt_signed_in=True)

        action_imported = next_action(LectureState.IMPORTED, caps)
        self.assertEqual(action_imported.action_type, "prepare_local")

        action_prepared = next_action(LectureState.PREPARED, caps)
        self.assertEqual(action_prepared.action_type, "build_package")

        action_package_ready = next_action(LectureState.PACKAGE_READY, caps)
        self.assertEqual(action_package_ready.action_type, "request_consent")

        action_lesson_ready = next_action(LectureState.LESSON_READY, caps)
        self.assertEqual(action_lesson_ready.action_type, "view_lesson")

        action_failed = next_action(LectureState.FAILED, caps)
        self.assertEqual(action_failed.action_type, "retry")

        action_partial = next_action(LectureState.RECOVERABLE_PARTIAL, caps)
        self.assertEqual(action_partial.action_type, "resume")

        action_downloading = next_action(LectureState.DOWNLOADING, caps)
        self.assertEqual(action_downloading.action_type, "cancel")

        action_generating = next_action(LectureState.GENERATING, caps)
        self.assertEqual(action_generating.action_type, "cancel")

    def test_resolve_lecture_state_from_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lec_dir = Path(temp_dir)

            # Initially empty -> IMPORTED
            self.assertEqual(resolve_lecture_state(lec_dir), LectureState.IMPORTED)

            # With .part file -> RECOVERABLE_PARTIAL
            (lec_dir / "audio.mp4.part").write_text("partial", encoding="utf-8")
            self.assertEqual(resolve_lecture_state(lec_dir), LectureState.RECOVERABLE_PARTIAL)
            (lec_dir / "audio.mp4.part").unlink()

            # With transcript.json -> PREPARED
            (lec_dir / "transcript.json").write_text('[{"text": "hi"}]', encoding="utf-8")
            self.assertEqual(resolve_lecture_state(lec_dir), LectureState.PREPARED)

            # With context package files -> PACKAGE_READY
            (lec_dir / "lesson-context.md").write_text("# Context", encoding="utf-8")
            (lec_dir / "lesson-prompt.md").write_text("# Prompt", encoding="utf-8")
            self.assertEqual(resolve_lecture_state(lec_dir), LectureState.PACKAGE_READY)

            # With finished lesson.md -> LESSON_READY
            (lec_dir / "lesson.md").write_text("# Finished lesson", encoding="utf-8")
            self.assertEqual(resolve_lecture_state(lec_dir), LectureState.LESSON_READY)


if __name__ == "__main__":
    unittest.main()
