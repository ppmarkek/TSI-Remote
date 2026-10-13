from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from konspekt.study_progress import KNOWN_FROM_BOX, MAX_BOX, StudyStore


class StudyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "study.json"
        self.store = StudyStore(self.path)
        self.today = date(2026, 10, 1)

    def test_new_cards_are_due_and_counted_as_new(self) -> None:
        stats = self.store.deck_stats("lec", ["a", "b"], today=self.today)

        self.assertEqual(
            stats.to_dict(), {"total": 2, "new": 2, "learning": 0, "known": 0, "due": 2}
        )
        self.assertTrue(self.store.card_state("lec", "a").is_new)

    def test_remembered_card_moves_up_and_is_scheduled_later(self) -> None:
        first = self.store.record_review("lec", "a", True, today=self.today)
        self.assertEqual(first.box, 2)
        self.assertEqual(first.due, "2026-10-02")
        self.assertFalse(first.is_due(self.today))

        second = self.store.record_review("lec", "a", True, today=date(2026, 10, 2))
        self.assertEqual(second.box, KNOWN_FROM_BOX)
        self.assertEqual(second.due, "2026-10-05")
        stats = self.store.deck_stats("lec", ["a"], today=date(2026, 10, 2))
        self.assertEqual((stats.known, stats.due), (1, 0))

    def test_box_never_exceeds_the_maximum(self) -> None:
        state = None
        for _ in range(MAX_BOX + 3):
            state = self.store.record_review("lec", "a", True, today=self.today)
        assert state is not None
        self.assertEqual(state.box, MAX_BOX)

    def test_forgotten_card_returns_to_first_box_today(self) -> None:
        self.store.record_review("lec", "a", True, today=self.today)
        state = self.store.record_review("lec", "a", False, today=self.today)

        self.assertEqual((state.box, state.lapses, state.reviews), (1, 1, 2))
        self.assertTrue(state.is_due(self.today))
        self.assertEqual(self.store.due_card_ids("lec", ["a", "b"], today=self.today), ["a", "b"])

    def test_progress_survives_a_new_store_instance(self) -> None:
        self.store.record_review("lec", "a", True, today=self.today)
        self.store.set_reading_position("lec", 0.4567)

        reloaded = StudyStore(self.path)
        self.assertEqual(reloaded.card_state("lec", "a").box, 2)
        self.assertAlmostEqual(reloaded.reading_position("lec"), 0.4567)
        self.assertTrue(reloaded.last_opened("lec"))

    def test_reading_position_is_clamped_and_ignores_garbage(self) -> None:
        self.store.set_reading_position("lec", 3)
        self.assertEqual(self.store.reading_position("lec"), 1.0)
        self.store.set_reading_position("lec", "nonsense")  # type: ignore[arg-type]
        self.assertEqual(self.store.reading_position("lec"), 1.0)

    def test_reset_and_forget(self) -> None:
        self.store.record_review("lec", "a", True, today=self.today)
        self.store.reset_deck("lec")
        self.assertTrue(self.store.card_state("lec", "a").is_new)

        self.store.set_reading_position("lec", 0.5)
        self.store.forget_lecture("lec")
        self.assertEqual(self.store.reading_position("lec"), 0.0)

    def test_damaged_file_is_treated_as_empty(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        store = StudyStore(self.path)

        self.assertTrue(store.card_state("lec", "a").is_new)
        store.record_review("lec", "a", False, today=self.today)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["lectures"]["lec"]["cards"]["a"]["box"], 1)

    def test_preferences_are_validated(self) -> None:
        self.assertEqual(self.store.preferences()["theme"], "system")

        result = self.store.update_preferences(
            {"theme": "dark", "reading_font": "comic", "font_scale": 9, "evil": True}
        )

        self.assertEqual(result["theme"], "dark")
        self.assertEqual(result["reading_font"], "sans")
        self.assertEqual(result["font_scale"], 1.4)
        self.assertNotIn("evil", result)
        self.assertEqual(StudyStore(self.path).preferences()["theme"], "dark")


if __name__ == "__main__":
    unittest.main()
