"""Local study progress: spaced-repetition boxes, reading positions and UI preferences."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_text

STUDY_SCHEMA_VERSION = 1
MAX_BOX = 5
# Days until the next review after a successful answer, indexed by the new box.
BOX_INTERVAL_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 16}
KNOWN_FROM_BOX = 3

DEFAULT_PREFERENCES: dict[str, Any] = {
    "theme": "system",
    "reading_font": "sans",
    "font_scale": 1.0,
}
_ALLOWED_THEMES = {"system", "light", "dark"}
_ALLOWED_FONTS = {"sans", "serif"}


@dataclass(frozen=True)
class CardState:
    box: int = 0
    due: str = ""
    reviews: int = 0
    lapses: int = 0
    last: str = ""

    @property
    def is_new(self) -> bool:
        return self.reviews == 0

    def is_due(self, today: date) -> bool:
        if self.is_new or not self.due:
            return True
        try:
            return date.fromisoformat(self.due) <= today
        except ValueError:
            return True


@dataclass(frozen=True)
class DeckStats:
    total: int
    new: int
    learning: int
    known: int
    due: int

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "new": self.new,
            "learning": self.learning,
            "known": self.known,
            "due": self.due,
        }


class StudyStore:
    """A small JSON store kept next to the library.

    Every write replaces the file atomically. A damaged file never blocks the
    application: it is treated as empty and overwritten on the next change.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        data: dict[str, Any] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
        if not isinstance(data.get("lectures"), dict):
            data["lectures"] = {}
        if not isinstance(data.get("preferences"), dict):
            data["preferences"] = {}
        data["schema_version"] = STUDY_SCHEMA_VERSION
        self._data = data
        return data

    def _save(self) -> None:
        data = self._load()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.path,
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (AtomicIOError, OSError):
            # Progress is a convenience; losing one write must not break study.
            pass

    def _lecture(self, lecture_key: str, *, create: bool = False) -> dict[str, Any]:
        lectures = self._load()["lectures"]
        entry = lectures.get(lecture_key)
        if not isinstance(entry, dict):
            entry = {}
            if create:
                lectures[lecture_key] = entry
        if create:
            if not isinstance(entry.get("cards"), dict):
                entry["cards"] = {}
        return entry

    # -------------------------------------------------------------------- cards
    def card_state(self, lecture_key: str, card_id: str) -> CardState:
        with self._lock:
            cards = self._lecture(lecture_key).get("cards", {})
            raw = cards.get(card_id) if isinstance(cards, dict) else None
            return _card_from_dict(raw)

    def record_review(
        self,
        lecture_key: str,
        card_id: str,
        remembered: bool,
        *,
        today: date | None = None,
    ) -> CardState:
        current_day = today or date.today()
        with self._lock:
            entry = self._lecture(lecture_key, create=True)
            previous = _card_from_dict(entry["cards"].get(card_id))
            if remembered:
                box = 2 if previous.is_new else min(MAX_BOX, max(1, previous.box) + 1)
                lapses = previous.lapses
            else:
                box = 1
                lapses = previous.lapses + 1
            due = current_day + timedelta(days=BOX_INTERVAL_DAYS[box])
            state = CardState(
                box=box,
                due=due.isoformat(),
                reviews=previous.reviews + 1,
                lapses=lapses,
                last=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            entry["cards"][card_id] = _card_to_dict(state)
            entry["last_studied"] = state.last
            self._save()
            return state

    def reset_deck(self, lecture_key: str) -> None:
        with self._lock:
            entry = self._lecture(lecture_key)
            if entry.get("cards"):
                entry["cards"] = {}
                self._save()

    def deck_stats(
        self,
        lecture_key: str,
        card_ids: list[str] | tuple[str, ...],
        *,
        today: date | None = None,
    ) -> DeckStats:
        current_day = today or date.today()
        new = learning = known = due = 0
        with self._lock:
            for card_id in card_ids:
                state = self.card_state(lecture_key, card_id)
                if state.is_new:
                    new += 1
                elif state.box >= KNOWN_FROM_BOX:
                    known += 1
                else:
                    learning += 1
                if state.is_due(current_day):
                    due += 1
        return DeckStats(total=len(card_ids), new=new, learning=learning, known=known, due=due)

    def due_card_ids(
        self,
        lecture_key: str,
        card_ids: list[str] | tuple[str, ...],
        *,
        today: date | None = None,
    ) -> list[str]:
        current_day = today or date.today()
        with self._lock:
            return [
                card_id
                for card_id in card_ids
                if self.card_state(lecture_key, card_id).is_due(current_day)
            ]

    # ------------------------------------------------------------------ reading
    def reading_position(self, lecture_key: str) -> float:
        with self._lock:
            value = self._lecture(lecture_key).get("reading_position", 0.0)
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def set_reading_position(self, lecture_key: str, position: float) -> None:
        try:
            bounded = min(1.0, max(0.0, float(position)))
        except (TypeError, ValueError):
            return
        with self._lock:
            entry = self._lecture(lecture_key, create=True)
            entry["reading_position"] = round(bounded, 4)
            entry["last_opened"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._save()

    def last_opened(self, lecture_key: str) -> str:
        with self._lock:
            return str(self._lecture(lecture_key).get("last_opened", ""))

    def forget_lecture(self, lecture_key: str) -> None:
        with self._lock:
            lectures = self._load()["lectures"]
            if lecture_key in lectures:
                del lectures[lecture_key]
                self._save()

    # -------------------------------------------------------------- preferences
    def preferences(self) -> dict[str, Any]:
        with self._lock:
            stored = self._load()["preferences"]
            result = dict(DEFAULT_PREFERENCES)
            result.update(_validated_preferences(stored))
            return result

    def update_preferences(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            stored = self._load()["preferences"]
            stored.update(_validated_preferences(changes))
            self._save()
            return self.preferences()


def _validated_preferences(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}
    result: dict[str, Any] = {}
    theme = values.get("theme")
    if theme in _ALLOWED_THEMES:
        result["theme"] = theme
    font = values.get("reading_font")
    if font in _ALLOWED_FONTS:
        result["reading_font"] = font
    scale = values.get("font_scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        result["font_scale"] = round(min(1.4, max(0.85, float(scale))), 2)
    return result


def _card_from_dict(raw: Any) -> CardState:
    if not isinstance(raw, dict):
        return CardState()
    try:
        box = int(raw.get("box", 0))
        reviews = int(raw.get("reviews", 0))
        lapses = int(raw.get("lapses", 0))
    except (TypeError, ValueError):
        return CardState()
    return CardState(
        box=min(MAX_BOX, max(0, box)),
        due=str(raw.get("due", "")),
        reviews=max(0, reviews),
        lapses=max(0, lapses),
        last=str(raw.get("last", "")),
    )


def _card_to_dict(state: CardState) -> dict[str, Any]:
    return {
        "box": state.box,
        "due": state.due,
        "reviews": state.reviews,
        "lapses": state.lapses,
        "last": state.last,
    }
