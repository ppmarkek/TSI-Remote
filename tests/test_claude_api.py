from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2

from konspekt.claude_api import ClaudeApiError, stream_claude_text
from konspekt.job_runner import CancellationToken, JobCancelledError


class FakeStream:
    def __init__(self, chunks: list[str], stop_reason: str, *, on_chunk: Any = None) -> None:
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.on_chunk = on_chunk
        self.closed = False

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True

    @property
    def text_stream(self):
        for chunk in self.chunks:
            if self.on_chunk is not None:
                self.on_chunk()
            if self.closed:
                raise httpx2.ReadError("stream closed")
            yield chunk

    def get_final_message(self) -> Any:
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            model="claude-opus-5-5",
            content=[
                SimpleNamespace(type="thinking", thinking="hidden"),
                SimpleNamespace(type="text", text="".join(self.chunks)),
            ],
        )

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, stream: FakeStream | None = None, error: Exception | None = None) -> None:
        self._stream = stream
        self._error = error
        self.params: dict[str, Any] = {}
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._open))

    def _open(self, **params: Any) -> FakeStream:
        self.params = params
        if self._error is not None:
            raise self._error
        assert self._stream is not None
        return self._stream


def _status_error(cls: type[anthropic.APIStatusError], code: int, body: Any = None) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(code, request=request), body=body)


class StreamClaudeTextTests(unittest.TestCase):
    def test_streams_deltas_and_returns_final_text(self) -> None:
        client = FakeClient(FakeStream(["# Тема", "\n\nТекст"], "end_turn"))
        seen: list[str] = []

        reply = stream_claude_text(
            "",
            "claude-opus-5-5",
            [{"role": "user", "content": "Привет"}],
            system="Будь точным.",
            effort="high",
            on_text=seen.append,
            client=client,
        )

        self.assertEqual(seen, ["# Тема", "\n\nТекст"])
        self.assertEqual(reply.text, "# Тема\n\nТекст")
        self.assertEqual(reply.stop_reason, "end_turn")
        self.assertEqual(client.params["model"], "claude-opus-5-5")
        self.assertEqual(client.params["system"], "Будь точным.")
        self.assertEqual(client.params["output_config"], {"effort": "high"})
        self.assertEqual(client.params["fallbacks"], "default")
        self.assertEqual(client.params["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(client.params["messages"], [{"role": "user", "content": "Привет"}])

    def test_older_models_get_neither_effort_nor_fallbacks(self) -> None:
        client = FakeClient(FakeStream(["ok"], "end_turn"))

        stream_claude_text(
            "", "claude-haiku-4-5", [{"role": "user", "content": "x"}], effort="high", client=client
        )

        self.assertNotIn("output_config", client.params)
        self.assertNotIn("fallbacks", client.params)
        self.assertNotIn("betas", client.params)
        self.assertNotIn("system", client.params)

    def test_refusal_is_reported_instead_of_returning_partial_text(self) -> None:
        client = FakeClient(FakeStream(["частично"], "refusal"))

        with self.assertRaisesRegex(ClaudeApiError, "отказался"):
            stream_claude_text(
                "", "claude-opus-5-5", [{"role": "user", "content": "x"}], client=client
            )

    def test_missing_key_is_reported_before_any_request(self) -> None:
        with self.assertRaisesRegex(ClaudeApiError, "API-ключ Claude"):
            stream_claude_text("  ", "claude-opus-5-5", [{"role": "user", "content": "x"}])

    def test_api_errors_become_readable_messages(self) -> None:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        cases = [
            (_status_error(anthropic.AuthenticationError, 401), "ключ Claude не принят"),
            (_status_error(anthropic.NotFoundError, 404), "«claude-opus-5-5» не найдена"),
            (_status_error(anthropic.RateLimitError, 429), "лимита"),
            (_status_error(anthropic.InternalServerError, 500), "временно"),
            (
                _status_error(
                    anthropic.BadRequestError,
                    400,
                    {"error": {"message": "Your credit balance is too low."}},
                ),
                "код 400\\): Your credit balance is too low.",
            ),
            (anthropic.APITimeoutError(request=request), "слишком долго"),
            (anthropic.APIConnectionError(request=request), "подключиться"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                client = FakeClient(error=error)
                with self.assertRaisesRegex(ClaudeApiError, expected):
                    stream_claude_text(
                        "", "claude-opus-5-5", [{"role": "user", "content": "x"}], client=client
                    )

    def test_cancellation_closes_the_stream(self) -> None:
        token = CancellationToken()
        stream = FakeStream(["раз", "два", "три"], "end_turn")
        received: list[str] = []

        def on_text(delta: str) -> None:
            received.append(delta)
            token.cancel()

        with self.assertRaises(JobCancelledError):
            stream_claude_text(
                "",
                "claude-opus-5-5",
                [{"role": "user", "content": "x"}],
                on_text=on_text,
                cancellation_token=token,
                client=FakeClient(stream),
            )

        self.assertTrue(stream.closed)
        self.assertEqual(received, ["раз"])


if __name__ == "__main__":
    unittest.main()
