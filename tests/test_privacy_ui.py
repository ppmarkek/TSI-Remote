from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.diagnostics import collect_system_diagnostics
from konspekt.outbound_context import build_outbound_context
from konspekt.platform_services import PlatformAppPaths


class PrivacyUITests(unittest.TestCase):
    def test_outbound_context_sanitization_removes_all_identifying_leakage(self) -> None:
        from konspekt.outbound_context import OutboundContextError

        # Leaked meeting ID or URL causes immediate rejection
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                title="Dirty Lecture",
                transcript_text="Found meetingId=4a7c8b9d0e in transcription.",
                meeting_id="4a7c8b9d0e",
            )

        # Leaked arbitrary URL with query parameters causes rejection
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                title="Dirty Lecture",
                transcript_text="Visit https://private.invalid/token=DO_NOT_SEND for materials.",
            )

        # Leaked bearer token causes immediate rejection
        with self.assertRaises(OutboundContextError):
            build_outbound_context(
                title="Dirty Lecture",
                transcript_text="Found Bearer sk-proj-1234567890abcdef in transcription.",
            )

        # Clean text builds successfully and remains clean
        outbound = build_outbound_context(
            title="Clean Lecture",
            transcript_text="Understanding computational complexity and Big O notation.",
            slides_text="Slide 1: Asymptotic bounds",
            ocr_notes_text="12.5s: Definition of O(N)",
        )
        self.assertNotIn("https://", outbound.sanitized_text)
        self.assertNotIn("meetingId", outbound.sanitized_text)
        self.assertNotIn("Bearer", outbound.sanitized_text)

    def test_diagnostics_collection_never_includes_secrets_or_user_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_paths = PlatformAppPaths(test_root=Path(temp_dir))
            diagnostics = collect_system_diagnostics(test_paths)

            self.assertEqual(diagnostics["status"], "ok")
            serialized = str(diagnostics)
            self.assertNotIn("Bearer", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("password", serialized)
            self.assertNotIn("meetingId", serialized)


if __name__ == "__main__":
    unittest.main()
