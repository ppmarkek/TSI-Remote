from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from konspekt.chat import (
    MAX_HISTORY_CHARS,
    ChatError,
    ChatReply,
    ChatService,
    ChatStore,
    history_for_api,
    stream_chat_reply,
)
from konspekt.job_runner import CancellationToken, JobCancelledError
from konspekt.settings import AppSettings


class FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str], payload: Any = None) -> None:
        self.status_code = status_code
        self.lines = lines
        self.payload = payload or {}
        self.closed = False

    def iter_lines(self):
        for line in self.lines:
            yield line.encode("utf-8")

    def json(self) -> Any:
        return self.payload

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeStreamResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeStreamResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response

    def close(self) -> None:
        pass


def _sse(*events: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event.get('type', 'message')}", f"data: {json.dumps(event)}", ""]
    return lines


class ChatStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "chats.json"
        self.store = ChatStore(self.path)

    def test_first_message_starts_a_titled_chat_and_persists(self) -> None:
        chat = self.store.add_message(None, "user", "## Объясни **рекурсию**\nподробно")
        self.store.add_message(chat["id"], "assistant", "Рекурсия — это…", model="m-1")

        reloaded = ChatStore(self.path).get_chat(chat["id"])
        self.assertEqual(reloaded["title"], "Объясни рекурсию подробно")
        self.assertEqual([m["role"] for m in reloaded["messages"]], ["user", "assistant"])
        self.assertEqual(reloaded["messages"][1]["model"], "m-1")

    def test_long_titles_are_shortened(self) -> None:
        chat = self.store.add_message(None, "user", "слово " * 40)
        self.assertLessEqual(len(chat["title"]), 60)
        self.assertTrue(chat["title"].endswith("…"))

    def test_lists_newest_first_and_supports_rename_and_delete(self) -> None:
        first = self.store.add_message(None, "user", "первый")
        second = self.store.add_message(None, "user", "второй")
        self.store._load()["chats"][0]["updated_at"] = "2020-01-01T00:00:00+00:00"

        self.assertEqual([c["id"] for c in self.store.list_chats()], [second["id"], first["id"]])
        self.assertEqual(self.store.rename(first["id"], "  Новое   имя ")["title"], "Новое имя")
        with self.assertRaises(ChatError):
            self.store.rename(first["id"], "   ")
        self.store.delete(first["id"])
        self.assertEqual([c["id"] for c in self.store.list_chats()], [second["id"]])
        with self.assertRaises(ChatError):
            self.store.get_chat(first["id"])

    def test_drop_trailing_assistant_keeps_the_question(self) -> None:
        chat = self.store.add_message(None, "user", "вопрос")
        self.store.add_message(chat["id"], "assistant", "ответ")

        remaining = self.store.drop_trailing_assistant(chat["id"])

        self.assertEqual([m["role"] for m in remaining["messages"]], ["user"])

    def test_damaged_file_is_treated_as_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(ChatStore(self.path).list_chats(), [])


class HistoryTests(unittest.TestCase):
    def test_merges_repeated_roles_and_starts_with_user(self) -> None:
        messages = [
            {"role": "assistant", "content": "приветствие"},
            {"role": "user", "content": "раз"},
            {"role": "user", "content": "два"},
            {"role": "assistant", "content": "ответ"},
            {"role": "user", "content": "  "},
        ]

        self.assertEqual(
            history_for_api(messages),
            [
                {"role": "user", "content": "раз\n\nдва"},
                {"role": "assistant", "content": "ответ"},
            ],
        )

    def test_drops_oldest_turns_beyond_the_budget(self) -> None:
        big = "x" * (MAX_HISTORY_CHARS // 2)
        messages = [
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": "последний вопрос"},
        ]

        history = history_for_api(messages)

        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[-1]["content"], "последний вопрос")
        self.assertLessEqual(sum(len(m["content"]) for m in history), MAX_HISTORY_CHARS)


class ProviderStreamingTests(unittest.TestCase):
    def test_openai_streams_output_text_deltas(self) -> None:
        response = FakeStreamResponse(
            200,
            _sse(
                {"type": "response.created"},
                {"type": "response.output_text.delta", "delta": "При"},
                {"type": "response.output_text.delta", "delta": "вет"},
                {"type": "response.completed"},
            ),
        )
        session = FakeSession(response)
        seen: list[str] = []
        settings = AppSettings(api_provider="openai", api_model="gpt-test", api_key="sk-test")
        history = [{"role": "user", "content": "Привет"}]

        reply = stream_chat_reply(
            settings,
            history,
            on_text=seen.append,
            cancellation_token=CancellationToken(),
            session=session,
        )

        self.assertEqual(reply, ChatReply(text="Привет", model="gpt-test"))
        self.assertEqual(seen, ["При", "вет"])
        call = session.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/responses")
        self.assertTrue(call["stream"])
        self.assertEqual(call["json"]["input"], history)
        self.assertFalse(call["json"]["store"])
        self.assertTrue(response.closed)

    def test_deepseek_streams_choice_deltas_until_done(self) -> None:
        lines = [
            "data: " + json.dumps({"choices": [{"delta": {"role": "assistant"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "Да"}}]}),
            "data: [DONE]",
            "data: " + json.dumps({"choices": [{"delta": {"content": "лишнее"}}]}),
        ]
        session = FakeSession(FakeStreamResponse(200, lines))
        settings = AppSettings(
            api_provider="deepseek", api_model="deepseek-v4-flash", api_key="sk-test"
        )

        reply = stream_chat_reply(
            settings,
            [{"role": "user", "content": "Вопрос"}],
            on_text=lambda _: None,
            cancellation_token=CancellationToken(),
            session=session,
        )

        self.assertEqual(reply.text, "Да")
        payload = session.calls[0]["json"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_http_errors_are_reported_to_the_user(self) -> None:
        session = FakeSession(FakeStreamResponse(401, []))
        settings = AppSettings(api_provider="openai", api_model="gpt-test", api_key="sk-bad")

        with self.assertRaisesRegex(ChatError, "ключ не принят"):
            stream_chat_reply(
                settings,
                [{"role": "user", "content": "x"}],
                on_text=lambda _: None,
                cancellation_token=CancellationToken(),
                session=session,
            )

    def test_stream_error_events_are_reported(self) -> None:
        response = FakeStreamResponse(
            200, _sse({"type": "error", "error": {"message": "model overloaded"}})
        )
        settings = AppSettings(api_provider="openai", api_model="gpt-test", api_key="sk-test")

        with self.assertRaisesRegex(ChatError, "model overloaded"):
            stream_chat_reply(
                settings,
                [{"role": "user", "content": "x"}],
                on_text=lambda _: None,
                cancellation_token=CancellationToken(),
                session=FakeSession(response),
            )

    def test_unconfigured_settings_are_rejected(self) -> None:
        with self.assertRaisesRegex(ChatError, "API-ключ"):
            stream_chat_reply(
                AppSettings(api_provider="claude", api_model="claude-opus-5-5", api_key=""),
                [{"role": "user", "content": "x"}],
                on_text=lambda _: None,
                cancellation_token=CancellationToken(),
            )


class ScriptedStreamer:
    """Streams the given chunks; optionally waits for a release between them."""

    def __init__(self, chunks: list[str], *, error: Exception | None = None, hold: bool = False):
        self.chunks = chunks
        self.error = error
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.histories: list[list[dict[str, str]]] = []

    def __call__(self, settings, history, *, on_text, cancellation_token) -> ChatReply:
        self.histories.append(history)
        for index, chunk in enumerate(self.chunks):
            if index:
                while not self.release.wait(0.01):
                    cancellation_token.check_cancelled()
            cancellation_token.check_cancelled()
            on_text(chunk)
        if self.error is not None:
            raise self.error
        return ChatReply(text="".join(self.chunks), model=settings.api_model)


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = ChatStore(Path(self._temp.name) / "chats.json")
        self.settings = AppSettings(
            api_provider="claude", api_model="claude-opus-5-5", api_key="sk-ant-test"
        )

    def service(self, streamer: Any, settings: AppSettings | None = None) -> ChatService:
        service = ChatService(self.store, lambda: settings or self.settings, streamer=streamer)
        self.addCleanup(service.shutdown)
        return service

    @staticmethod
    def finish(service: ChatService, chat_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 5
        seen = 0
        while time.monotonic() < deadline:
            result = service.poll(chat_id, seen, timeout=1)
            if not result["running"]:
                return result
            seen = result["length"]
        raise AssertionError("chat run did not finish")

    def test_send_streams_and_stores_the_answer(self) -> None:
        service = self.service(ScriptedStreamer(["**Привет**", ", это ответ"]))

        started = service.send(None, "  Привет!  ")
        result = self.finish(service, started["id"])

        self.assertEqual(result["error"], "")
        messages = result["chat"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Привет!")
        self.assertEqual(messages[1]["content"], "**Привет**, это ответ")
        self.assertIn("<strong>Привет</strong>", messages[1]["html"])
        self.assertEqual(messages[1]["model"], "claude-opus-5-5")
        self.assertFalse(service.is_running(started["id"]))

    def test_follow_up_sends_the_whole_conversation(self) -> None:
        streamer = ScriptedStreamer(["ответ"])
        service = self.service(streamer)
        chat_id = service.send(None, "первый")["id"]
        self.finish(service, chat_id)

        service.send(chat_id, "второй")
        self.finish(service, chat_id)

        self.assertEqual(
            streamer.histories[-1],
            [
                {"role": "user", "content": "первый"},
                {"role": "assistant", "content": "ответ"},
                {"role": "user", "content": "второй"},
            ],
        )

    def test_poll_reports_partial_text_while_running(self) -> None:
        streamer = ScriptedStreamer(["Часть", " и конец"], hold=True)
        service = self.service(streamer)
        chat_id = service.send(None, "вопрос")["id"]

        partial = service.poll(chat_id, 0, timeout=2)
        self.assertTrue(partial["running"])
        self.assertEqual(partial["length"], len("Часть"))
        self.assertIn("Часть", partial["html"])
        with self.assertRaisesRegex(ChatError, "Дождись"):
            service.send(chat_id, "ещё")

        streamer.release.set()
        self.assertEqual(
            self.finish(service, chat_id)["chat"]["messages"][-1]["content"], "Часть и конец"
        )

    def test_stop_keeps_the_partial_answer(self) -> None:
        streamer = ScriptedStreamer(["Начало", " продолжение"], hold=True)
        service = self.service(streamer)
        chat_id = service.send(None, "вопрос")["id"]
        service.poll(chat_id, 0, timeout=2)

        service.stop(chat_id)
        result = self.finish(service, chat_id)

        self.assertTrue(result["stopped"])
        last = result["chat"]["messages"][-1]
        self.assertEqual(last["content"], "Начало")
        self.assertTrue(last["stopped"])

    def test_errors_are_reported_and_retry_answers_again(self) -> None:
        failing = ScriptedStreamer([], error=ChatError("Сервис недоступен."))
        service = self.service(failing)
        chat_id = service.send(None, "вопрос")["id"]

        result = self.finish(service, chat_id)
        self.assertEqual(result["error"], "Сервис недоступен.")
        self.assertEqual([m["role"] for m in result["chat"]["messages"]], ["user"])

        service._streamer = ScriptedStreamer(["Теперь получилось"])
        service.retry(chat_id)
        result = self.finish(service, chat_id)
        self.assertEqual(result["chat"]["messages"][-1]["content"], "Теперь получилось")

        service._streamer = ScriptedStreamer(["Другой вариант"])
        service.retry(chat_id)
        messages = self.finish(service, chat_id)["chat"]["messages"]
        self.assertEqual([m["content"] for m in messages], ["вопрос", "Другой вариант"])

    def test_requires_configured_api_and_a_message(self) -> None:
        service = self.service(ScriptedStreamer(["x"]), AppSettings(api_key=""))
        with self.assertRaisesRegex(ChatError, "API-ключ"):
            service.send(None, "вопрос")
        configured = self.service(ScriptedStreamer(["x"]))
        with self.assertRaisesRegex(ChatError, "Напиши"):
            configured.send(None, "   ")
        self.assertEqual(self.store.list_chats(), [])

    def test_shutdown_cancels_running_answers(self) -> None:
        streamer = ScriptedStreamer(["a", "b"], hold=True)
        service = self.service(streamer)
        chat_id = service.send(None, "вопрос")["id"]
        service.poll(chat_id, 0, timeout=2)

        service.shutdown()

        self.assertTrue(self.finish(service, chat_id)["stopped"])

    def test_cancelled_streamer_without_text_saves_nothing(self) -> None:
        def cancelled(settings, history, *, on_text, cancellation_token):
            raise JobCancelledError("stop")

        service = self.service(cancelled)
        chat_id = service.send(None, "вопрос")["id"]

        result = self.finish(service, chat_id)

        self.assertTrue(result["stopped"])
        self.assertEqual(len(result["chat"]["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
