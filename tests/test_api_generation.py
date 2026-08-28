from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import requests

from konspekt.api_generation import ApiGenerationError, generate_lesson_via_api
from konspekt.bbb_import import BBBRecording
from konspekt.settings import AppSettings


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"HTTP {self.status_code}",
                response=self,  # type: ignore[arg-type]
            )


class FakeSession:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: requests.RequestException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("fake response was not configured")
        return self.response


class ApiGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="meeting-api",
            source_url="https://bbb.example.test/playback?meetingId=meeting-api",
            title="API lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://bbb.example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        self.settings = AppSettings(
            api_provider="openai",
            api_model="gpt-5.6-luna",
            api_key="sk-test-only",
        )

    @staticmethod
    def _write_context(directory: Path) -> None:
        (directory / "lesson-context.md").write_text(
            "# Lecture context\n\nUseful local material.\n",
            encoding="utf-8",
        )
        (directory / "lesson-prompt.md").write_text(
            "Create a structured lesson.",
            encoding="utf-8",
        )

    def test_parses_openai_response_and_saves_the_lesson(self) -> None:
        session = FakeSession(FakeResponse(200, {"output_text": "# Generated lesson"}))
        progress_updates: list[tuple[int, str]] = []

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            result = generate_lesson_via_api(
                self.recording,
                self.settings,
                directory=directory,
                progress=lambda percent, message: progress_updates.append((percent, message)),
                session=session,
            )
            lesson = result.saved_lesson.path.read_text(encoding="utf-8")

        self.assertEqual(lesson, "# Generated lesson\n")
        self.assertEqual(result.model, "gpt-5.6-luna")
        self.assertEqual(progress_updates[-1][0], 100)
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(call["timeout"], (20, 300))
        self.assertEqual(call["json"]["model"], "gpt-5.6-luna")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test-only")
        self.assertIn("Useful local material", call["json"]["input"])

    def test_reports_invalid_api_key_without_exposing_it(self) -> None:
        session = FakeSession(FakeResponse(401, {"error": {"message": "unauthorized"}}))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            with self.assertRaises(ApiGenerationError) as raised:
                generate_lesson_via_api(
                    self.recording,
                    self.settings,
                    directory=directory,
                    session=session,
                )

        message = str(raised.exception)
        self.assertIn("API", message)
        self.assertNotIn("sk-test-only", message)

    def test_maps_non_json_auth_error_by_status(self) -> None:
        session = FakeSession(FakeResponse(401, "<html>Unauthorized</html>"))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            with self.assertRaises(ApiGenerationError) as raised:
                generate_lesson_via_api(
                    self.recording,
                    self.settings,
                    directory=directory,
                    session=session,
                )

        self.assertIn("ключ", str(raised.exception).lower())

    def test_reports_provider_limit(self) -> None:
        session = FakeSession(FakeResponse(429, {"error": {"message": "quota"}}))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            with self.assertRaises(ApiGenerationError) as raised:
                generate_lesson_via_api(
                    self.recording,
                    self.settings,
                    directory=directory,
                    session=session,
                )

        self.assertIn("лимит", str(raised.exception).lower())

    def test_uses_deepseek_chat_format_and_removes_source_identifiers(self) -> None:
        settings = AppSettings(
            api_provider="deepseek",
            api_model="deepseek-v4-flash",
            api_key="deepseek-test-only",
        )
        session = FakeSession(
            FakeResponse(
                200,
                {"choices": [{"message": {"content": "# DeepSeek lesson"}}]},
            )
        )

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-context.md").write_text(
                "# Контекст\n\n## Источник\n"
                "- BBB-запись: https://private.example.test/playback\n"
                "- Идентификатор: `private-meeting-id`\n\n"
                "## Транскрипция по времени\n\nUseful local material.\n",
                encoding="utf-8",
            )
            (directory / "lesson-prompt.md").write_text(
                "Create a structured lesson.",
                encoding="utf-8",
            )

            result = generate_lesson_via_api(
                self.recording,
                settings,
                directory=directory,
                session=session,
            )

        call = session.calls[0]
        self.assertEqual(call["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(call["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(call["json"]["thinking"], {"type": "disabled"})
        sent_text = call["json"]["messages"][-1]["content"]
        self.assertIn("Useful local material", sent_text)
        self.assertNotIn("private.example.test", sent_text)
        self.assertNotIn("private-meeting-id", sent_text)
        self.assertEqual(result.saved_lesson.path.name, "lesson.md")

    def test_rejects_success_response_without_lesson_text(self) -> None:
        session = FakeSession(FakeResponse(200, {"output": []}))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            with self.assertRaises(ApiGenerationError):
                generate_lesson_via_api(
                    self.recording,
                    self.settings,
                    directory=directory,
                    session=session,
                )

    def test_normalises_request_timeout(self) -> None:
        session = FakeSession(error=requests.Timeout("upstream timed out"))

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_context(directory)

            with self.assertRaises(ApiGenerationError) as raised:
                generate_lesson_via_api(
                    self.recording,
                    self.settings,
                    directory=directory,
                    session=session,
                )

        self.assertIn("API", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
