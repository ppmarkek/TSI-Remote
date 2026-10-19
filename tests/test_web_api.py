from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from konspekt.bbb_import import BBBRecording
from konspekt.chat import ChatReply
from konspekt.library_manager import save_to_library
from konspekt.settings import AppSettings
from konspekt.web.api import KonspektApi, UserFacingError, lecture_key

LESSON = """# Структуры данных

## Стек

Стек работает по принципу LIFO [00:01:05]. Операции push и pop выполняются за O(1).

## Мини-словарь

- **Стек** — структура данных LIFO.
- **Очередь** — структура данных FIFO.

## Вопросы для самопроверки

1. Что такое стек?
   Ответ: структура данных LIFO.
2. Где применяют кольцевой буфер?
"""


class KonspektApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.data_dir = Path(self._temp.name)
        self.recording = BBBRecording(
            meeting_id="meeting-1",
            source_url="local://lecture-1",
            title="Структуры данных",
            imported_at="2026-10-01T10:00:00Z",
            audio_video_url="",
            screen_video_url=None,
            slides=(),
        )
        save_to_library(self.recording, self.data_dir / "library.json")
        self.opened: list[str] = []
        self.api = KonspektApi(
            data_dir=self.data_dir,
            settings_path=self.data_dir / "settings.json",
            open_url=self.opened.append,
            reveal_path=lambda path: None,
        )
        self.addCleanup(self.api._shutdown)
        self.key = lecture_key(self.recording)

        self.directory = self.api._directory(self.recording)
        frames = self.directory / "frames"
        frames.mkdir(parents=True)
        (self.directory / "lesson.md").write_text(LESSON, encoding="utf-8")
        (self.directory / "audio.wav").write_bytes(b"RIFF0000WAVE")
        (self.directory / "transcript.json").write_text(
            json.dumps(
                {
                    "segments": [
                        {"start": 0.0, "end": 64.0, "text": "Сегодня говорим о стеке."},
                        {"start": 64.0, "end": 130.5, "text": "Стек работает по принципу LIFO."},
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (frames / "interval-seconds.txt").write_text("60", encoding="ascii")
        for number in range(1, 4):
            (frames / f"frame-{number:04d}.jpg").write_bytes(b"\xff\xd8\xff")

    def call(self, name: str, *args):
        result = getattr(self.api, name)(*args)
        self.assertTrue(result["ok"], result.get("error"))
        return result["data"]

    def test_library_lists_a_ready_lesson_with_its_deck(self) -> None:
        lectures = self.call("list_lectures")

        self.assertEqual(len(lectures), 1)
        lecture = lectures[0]
        self.assertEqual((lecture["key"], lecture["stage"]), (self.key, "lesson"))
        self.assertEqual(lecture["source"], "local")
        self.assertEqual(lecture["duration"], 130.5)
        self.assertGreaterEqual(lecture["reading_minutes"], 1)
        self.assertEqual(lecture["cards"]["total"], 4)
        self.assertEqual(lecture["cards"]["due"], 4)
        self.assertIsNone(lecture["task"])

    def test_lesson_payload_has_html_outline_and_media(self) -> None:
        lesson = self.call("get_lesson", self.key)

        self.assertIn('<h2 id="sec-2">Стек</h2>', lesson["html"])
        self.assertIn('data-t="65"', lesson["html"])
        self.assertEqual(lesson["headings"][1], {"level": 2, "title": "Стек", "anchor": "sec-2"})
        self.assertEqual(lesson["card_count"], 4)
        self.assertTrue(lesson["has_audio"])
        self.assertEqual((lesson["frame_count"], lesson["frame_interval"]), (3, 60))
        self.assertEqual(lesson["media_base"], f"media/{self.key}/")

    def test_cards_can_be_reviewed(self) -> None:
        deck = self.call("get_cards", self.key)
        cards = {card["front_html"]: card for card in deck["cards"]}
        stack = cards["Что такое стек?"]
        self.assertEqual(stack["kind"], "qa")
        self.assertEqual(stack["back_html"], "<p>структура данных LIFO.</p>")
        self.assertTrue(stack["is_new"] and stack["due"])

        result = self.call("review_card", self.key, stack["id"], True)

        self.assertEqual(result["box"], 2)
        self.assertEqual(result["stats"]["due"], 3)
        failed = self.api.review_card(self.key, "missing", True)
        self.assertFalse(failed["ok"])
        self.assertIn("Карточка не найдена", failed["error"])

    def test_review_queue_spans_lectures(self) -> None:
        queue = self.call("review_queue")

        self.assertEqual(queue["total_due"], 4)
        self.assertEqual(queue["cards"][0]["lecture_title"], "Структуры данных")
        self.assertEqual(queue["lectures"][0]["stats"]["total"], 4)

    def test_search_finds_text_inside_lessons(self) -> None:
        results = self.call("search", "принципу lifo")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["key"], self.key)
        self.assertFalse(results[0]["title_hit"])
        self.assertIn("принципу LIFO", results[0]["snippets"][0])
        self.assertEqual(self.call("search", "a"), [])

    def test_saving_a_lesson_refreshes_the_cache(self) -> None:
        self.call("get_lesson", self.key)
        self.call("save_lesson", self.key, "# Новый конспект\n\nТекст.")

        lesson = self.call("get_lesson", self.key)
        self.assertIn("Новый конспект", lesson["html"])
        self.assertEqual(lesson["card_count"], 0)

    def test_media_paths_stay_inside_the_lecture(self) -> None:
        self.assertEqual(self.api._media_path(self.key, "audio", ""), self.directory / "audio.wav")
        self.assertEqual(
            self.api._media_path(self.key, "frame", "125").name,
            "frame-0003.jpg",
        )
        self.assertEqual(self.api._media_path(self.key, "frame", "0").name, "frame-0001.jpg")
        self.assertIsNone(self.api._media_path(self.key, "frame", "../x"))
        self.assertIsNone(self.api._media_path(self.key, "other", ""))
        with self.assertRaises(UserFacingError):
            self.api._media_path("unknown", "audio", "")

    def test_only_web_links_are_opened(self) -> None:
        self.call("open_external", "https://example.com/page")
        self.assertEqual(self.opened, ["https://example.com/page"])

        refused = self.api.open_external("javascript:alert(1)")
        self.assertFalse(refused["ok"])
        self.assertEqual(self.opened, ["https://example.com/page"])

    def test_unknown_lecture_is_a_friendly_error(self) -> None:
        result = self.api.get_lesson("missing")

        self.assertEqual(
            result, {"ok": False, "error": "Лекция не найдена. Возможно, её уже удалили."}
        )

    def test_settings_offer_claude_as_a_provider(self) -> None:
        options = self.call("get_settings")["options"]

        self.assertIn({"id": "claude", "label": "Claude"}, options["providers"])
        self.assertEqual(options["default_models"]["claude"], "claude-opus-5-5")

    def test_chat_round_trip_through_the_bridge(self) -> None:
        def streamer(settings, history, *, on_text, cancellation_token):
            on_text("Стек — это ")
            on_text("**LIFO**.")
            return ChatReply(text="Стек — это **LIFO**.", model=settings.api_model)

        api = KonspektApi(
            data_dir=self.data_dir,
            settings_path=self.data_dir / "settings.json",
            open_url=self.opened.append,
            reveal_path=lambda path: None,
            chat_streamer=streamer,
        )
        self.addCleanup(api._shutdown)
        api._settings_cache = AppSettings(
            api_provider="claude", api_model="claude-opus-5-5", api_key="sk-ant-test"
        )
        self.api = api

        meta = self.call("chat_list")
        self.assertEqual(meta["chats"], [])
        self.assertTrue(meta["api_configured"])
        self.assertEqual((meta["provider_label"], meta["model"]), ("Claude", "claude-opus-5-5"))

        chat = self.call("chat_send", None, "Что такое стек?")
        result = self.call("chat_poll", chat["id"], 0)
        while result["running"]:
            result = self.call("chat_poll", chat["id"], result["length"])

        messages = result["chat"]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertIn("<strong>LIFO</strong>", messages[1]["html"])
        self.assertEqual(self.call("chat_list")["chats"][0]["title"], "Что такое стек?")

        renamed = self.call("chat_rename", chat["id"], "Стек")
        self.assertEqual(renamed["title"], "Стек")
        self.assertEqual(self.call("chat_get", chat["id"])["title"], "Стек")

        self.call("chat_delete", chat["id"])
        self.assertEqual(self.call("chat_list")["chats"], [])
        failed = api.chat_get(chat["id"])
        self.assertFalse(failed["ok"])
        self.assertIn("не найден", failed["error"])

    def test_chat_without_api_key_explains_what_to_do(self) -> None:
        self.api._settings_cache = AppSettings(api_key="")

        result = self.api.chat_send(None, "Привет")

        self.assertFalse(result["ok"])
        self.assertIn("API-ключ", result["error"])


if __name__ == "__main__":
    unittest.main()
