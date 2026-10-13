"""Flashcards from lesson notes.

Cards come from two kinds of sections that the lesson prompt asks for:

* «Вопросы для самопроверки» — a numbered list, each question followed by a
  line ``Ответ: …`` (other common layouts are understood as well: bold
  ``**Ответ:**``, an answer on the same line, a separate «Ответы» section);
* «Мини-словарь» / «Ключевые понятия» / «Глоссарий» — ``- **Термин** — определение``
  lines or a two-column table.

A question without an answer still becomes a card; its back is the most
relevant passage of the lesson, marked as a hint.
"""

from __future__ import annotations

import hashlib
import math
import re
import textwrap
from dataclasses import dataclass, field

from .markdown_html import (
    find_timestamps,
    is_table_delimiter,
    split_table_row,
    strip_inline_markdown,
)

__all__ = ["StudyCard", "extract_study_cards", "find_hint"]


@dataclass(frozen=True)
class StudyCard:
    card_id: str
    kind: str  # "qa" | "term" | "question"
    front: str
    back: str
    section: str
    hint: bool = False
    timestamps: tuple[int, ...] = ()


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_ORDERED = re.compile(r"^( *)(\d{1,3})[.)][ \t]+(.*)$")
_BULLET = re.compile(r"^( *)[-*+][ \t]+(.*)$")
_HR = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_NUMBERED_TEXT = re.compile(r"^(\d{1,3})[.)]\s+(.+)$", re.S)
_QUESTION_LABEL = re.compile(r"^вопрос\s*№?\s*(\d{1,3})\s*[.:)—–-]\s*(.+)$", re.I | re.S)

_EMPH = r"[*_]{0,3}"
_ANSWER_LINE = re.compile(
    rf"^(?:[-*+]\s+|>\s*)?{_EMPH}\s*(?:краткий\s+|правильный\s+)?(?:ответ|answer)\s*{_EMPH}"
    rf"\s*[:：.—–-]\s*{_EMPH}[ \t]*(.*)$",
    re.I | re.S,
)
_INLINE_ANSWER = re.compile(
    rf"\s+(?:[-—–]\s*)?{_EMPH}(?:краткий\s+)?ответ{_EMPH}\s*[:：—–-]\s*{_EMPH}\s*", re.I
)
_WHOLE_BOLD = re.compile(r"^(\*\*|__)(?=\S)(.+?)(?<=\S)\1$", re.S)
_BOLD_TERM = re.compile(r"^(\*\*|__)(.+?)\1\s*(.*)$", re.S)
_PLAIN_TERM = (
    re.compile(r"^(.{1,80}?)\s+[—–-]\s+(.+)$", re.S),
    re.compile(r"^([^:]{1,80}?):\s+(.+)$", re.S),
)

_ANSWERS_TITLE = re.compile(r"^(?:ответы\b|ключ\w*\s+к\s+ответам|answers\b)", re.I)
_QUESTIONS_TITLE = re.compile(
    r"самопровер|самоконтрол|провер\w*\s+себя|контрольн\w*\s+вопрос"
    r"|вопрос\w*\s+(?:для|к)\s+\w+|^вопросы\b|^self[- ]check|^review questions",
    re.I,
)
_TERMS_TITLE = re.compile(
    r"словар|глоссари|терминолог|(?:ключев|основн|важн)\w*\s+(?:поняти|термин|определени)"
    r"|^(?:термины|понятия|определения)\b|^glossary|^key terms",
    re.I,
)

_WORD = re.compile(r"[^\W\d_]+")
_STOPWORDS = frozenset(
    """
    такое такой такая такие таким каким какой какая какие каких каком какую каково какова
    каков почему зачем чтобы этого этот этом этой этим эти этих между отличается отличаются
    отличие отличия разница разницу назовите перечислите объясните опишите приведите
    расскажите поясните сравните укажите определите пример примеры примера примеров основные
    основной основная главные главный который которая которое которые которых если когда также
    можно нужно является являются быть есть было были будет лекции лекция лекцию лекцией него
    него свой своя свои чего сколько бывают используется используют используются нужен нужна
    нужны означает называется называют понимается понимают каждый каждая каждого всего более
    менее очень только после перед через какое какого what which does with that this from have
    """.split()
)

_MAX_FRONT = 400
_MAX_TERM = 120


# ======================================================================= public
def extract_study_cards(markdown_text: str) -> list[StudyCard]:
    """All flashcards of a lesson, in document order, without duplicates."""

    lines = _lines(markdown_text)
    owners, sections = _classify(lines)
    index = _HintIndex(lines, _hint_exclusions(owners, sections))

    answers: dict[int, str] = {}
    for number, section in enumerate(sections):
        if section.kind == "answers":
            for item in _parse_questions(_section_body(lines, owners, number, section)):
                text = _join_parts(item.front, item.back)
                if item.number is not None and text:
                    answers.setdefault(item.number, text)

    cards: dict[str, StudyCard] = {}

    def add(card: StudyCard) -> None:
        existing = cards.get(card.card_id)
        if existing is None or _card_rank(card) > _card_rank(existing):
            cards[card.card_id] = card

    for number, section in enumerate(sections):
        body = _section_body(lines, owners, number, section)
        if section.kind == "questions":
            for item in _parse_questions(body):
                front, back = item.front, item.back
                if not back and item.number is not None:
                    back = answers.get(item.number, "")
                add(_question_card(front, back, section.title, index))
        elif section.kind == "terms":
            for term, definition in _parse_terms(body):
                add(_term_card(term, definition, section.title, index))
    return list(cards.values())


def find_hint(markdown_text: str, question: str, max_blocks: int = 2) -> str:
    """The passages of a lesson that best match a question ("" if nothing fits)."""

    lines = _lines(markdown_text)
    owners, sections = _classify(lines)
    index = _HintIndex(lines, _hint_exclusions(owners, sections))
    return index.hint(question, max_blocks=max_blocks)


# ===================================================================== sections
@dataclass
class _Section:
    kind: str | None
    level: int
    title: str
    heading_line: int
    end: int


def _lines(markdown_text: str) -> list[str]:
    text = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.expandtabs(4).split("\n")


def _plain(text: str) -> str:
    return " ".join(strip_inline_markdown(text).split())


def _section_kind(title: str) -> str | None:
    lowered = title.casefold().strip(" .:")
    if _ANSWERS_TITLE.search(lowered):
        return "answers"
    if _QUESTIONS_TITLE.search(lowered):
        return "questions"
    if _TERMS_TITLE.search(lowered):
        return "terms"
    return None


def _classify(lines: list[str]) -> tuple[list[int | None], list[_Section]]:
    """Sections of the lesson and, per line, the card section it belongs to.

    Inner card sections win over outer ones, so «Самопроверка» → «Ответы»
    keeps the answers out of the questions."""

    headings: list[tuple[int, int, str]] = []
    fence: str | None = None
    for number, line in enumerate(lines):
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        heading = _HEADING.match(line)
        if heading and heading.group(2).strip():
            headings.append((number, len(heading.group(1)), _plain(heading.group(2))))

    sections: list[_Section] = []
    for position, (line_number, level, title) in enumerate(headings):
        end = len(lines)
        for other_line, other_level, _ in headings[position + 1 :]:
            if other_level <= level:
                end = other_line
                break
        sections.append(_Section(_section_kind(title), level, title, line_number, end))

    owners: list[int | None] = [None] * len(lines)
    order = sorted(
        (number for number, section in enumerate(sections) if section.kind),
        key=lambda number: (sections[number].level, sections[number].heading_line),
    )
    for number in order:
        section = sections[number]
        for line_number in range(section.heading_line, section.end):
            owners[line_number] = number
    return owners, sections


def _hint_exclusions(owners: list[int | None], sections: list[_Section]) -> set[int]:
    """Lines a hint must not come from: the questions themselves and their answers.
    The glossary stays searchable — a definition is a good hint."""

    return {
        number
        for number, owner in enumerate(owners)
        if owner is not None and sections[owner].kind != "terms"
    }


def _section_body(
    lines: list[str], owners: list[int | None], number: int, section: _Section
) -> list[str]:
    return [
        lines[line_number] if owners[line_number] == number else ""
        for line_number in range(section.heading_line + 1, section.end)
    ]


# ==================================================================== questions
@dataclass
class _Question:
    number: int | None
    front_lines: list[str]
    answer_lines: list[str] = field(default_factory=list)
    answered: bool = False
    front: str = ""
    back: str = ""


def _outside_code(lines: list[str]) -> list[str]:
    result: list[str] = []
    fence: str | None = None
    for line in lines:
        match = _FENCE.match(line)
        if match:
            marker = match.group(1)[0]
            fence = marker if fence is None else (None if fence == marker else fence)
            continue
        if fence is None:
            result.append(line)
    return result


def _parse_questions(body: list[str]) -> list[_Question]:
    prose = _outside_code(body)
    ordered_indents = [len(match.group(1)) for line in prose if (match := _ORDERED.match(line))]
    numbered = bool(ordered_indents)
    if numbered:
        base = min(ordered_indents)
    else:
        bullet_indents = [len(match.group(1)) for line in prose if (match := _BULLET.match(line))]
        base = min(bullet_indents) if bullet_indents else 0

    items: list[_Question] = []
    current: _Question | None = None
    fence: str | None = None

    def finish() -> None:
        nonlocal current
        if current is not None:
            _finish_question(current)
            if current.front:
                items.append(current)
        current = None

    for line in body:
        fence_match = _FENCE.match(line)
        if fence_match or fence is not None:
            if fence_match:
                marker = fence_match.group(1)[0]
                fence = marker if fence is None else (None if fence == marker else fence)
            if current is not None:
                (current.answer_lines if current.answered else current.front_lines).append(line)
            continue

        if not line.strip():
            if current is not None:
                (current.answer_lines if current.answered else current.front_lines).append("")
            continue

        heading = _HEADING.match(line)
        if heading:
            finish()
            title = _plain(heading.group(2))
            if title.endswith("?") or _QUESTION_LABEL.match(title):
                label = _QUESTION_LABEL.match(title)
                number = int(label.group(1)) if label else None
                current = _Question(number, [label.group(2) if label else heading.group(2)])
            continue

        if _HR.match(line):
            finish()
            continue

        if current is not None and not current.answered:
            answer = _ANSWER_LINE.match(line.strip())
            if answer:
                current.answered = True
                current.answer_lines.append(answer.group(1))
                continue

        start = _question_start(line, numbered=numbered, base=base)
        if start is not None:
            finish()
            current = _Question(start[0], [start[1]])
            continue

        if current is None:
            continue
        (current.answer_lines if current.answered else current.front_lines).append(line)

    finish()
    return items


def _question_start(line: str, *, numbered: bool, base: int) -> tuple[int | None, str] | None:
    match = _ORDERED.match(line)
    if match and len(match.group(1)) <= base + 1:
        return int(match.group(2)), match.group(3)
    if not numbered:
        bullet = _BULLET.match(line)
        if bullet and len(bullet.group(1)) <= base + 1:
            return None, bullet.group(2)
    stripped = line.strip()
    if _indent(line) > base + 1:
        return None
    plain = _plain(stripped)
    if stripped.startswith(("**", "__")):
        numbered_text = _NUMBERED_TEXT.match(plain)
        if numbered_text:
            return int(numbered_text.group(1)), numbered_text.group(2)
    label = _QUESTION_LABEL.match(plain)
    if label:
        return int(label.group(1)), label.group(2)
    return None


def _finish_question(item: _Question) -> None:
    front = _block_text(item.front_lines)
    back = _block_text(item.answer_lines) if item.answered else ""
    if not item.answered:
        split = _INLINE_ANSWER.search(front)
        if split and split.start() > 0:
            front, back = front[: split.start()], front[split.end() :]
    front = _unwrap_bold(front.strip())
    if len(front) > _MAX_FRONT:
        front = front[:_MAX_FRONT].rsplit(" ", 1)[0] + "…"
    item.front = front
    item.back = _unwrap_bold(back.strip()) if back.strip() else ""


def _question_card(front: str, back: str, section: str, index: _HintIndex) -> StudyCard:
    if back:
        timestamps = find_timestamps(back) or find_timestamps(front)
        if not timestamps:
            timestamps = index.timestamps(f"{front}\n{back}", strict=True)
        return StudyCard(
            card_id=_card_id("q", front),
            kind="qa",
            front=front,
            back=back,
            section=section,
            timestamps=tuple(timestamps[:3]),
        )
    hint = index.hint(front)
    timestamps = find_timestamps(hint) if hint else find_timestamps(front)
    return StudyCard(
        card_id=_card_id("q", front),
        kind="question",
        front=front,
        back=hint,
        section=section,
        hint=bool(hint),
        timestamps=tuple(timestamps[:3]),
    )


# ======================================================================== terms
def _parse_terms(body: list[str]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    position = 0
    total = len(body)
    while position < total:
        line = body[position]
        if not line.strip():
            position += 1
            continue

        if "|" in line and position + 1 < total and is_table_delimiter(body[position + 1]):
            header = split_table_row(line)
            position += 2
            while position < total and body[position].strip() and "|" in body[position]:
                cells = split_table_row(body[position])
                position += 1
                if len(header) < 2 or len(cells) < 2:
                    continue
                extra = [
                    f"**{_plain(header[column])}:** {cells[column]}"
                    for column in range(2, min(len(cells), len(header)))
                    if cells[column].strip()
                ]
                terms.append((cells[0], _join_parts(cells[1], "\n".join(extra))))
            continue

        bullet = _BULLET.match(line) or _ORDERED.match(line)
        if bullet:
            marker_indent = len(bullet.group(1))
            text = bullet.group(bullet.lastindex or 2)
        elif line.lstrip().startswith(("**", "__")):
            marker_indent = _indent(line)
            text = line.strip()
        else:
            position += 1
            continue

        extra: list[str] = []
        position += 1
        while position < total:
            following = body[position]
            if not following.strip() or _HEADING.match(following):
                break
            nested = _BULLET.match(following) or _ORDERED.match(following)
            if nested and len(nested.group(1)) <= marker_indent:
                break
            if (
                not nested
                and _indent(following) <= marker_indent
                and following.lstrip().startswith(("**", "__"))
            ):
                break
            extra.append(following.strip())
            position += 1

        parsed = _split_term(text.strip())
        if parsed is None:
            continue
        term, definition = parsed
        definition = _join_parts(definition, "\n".join(extra))
        if term and definition:
            terms.append((term, definition))
    return terms


def _split_term(text: str) -> tuple[str, str] | None:
    bold = _BOLD_TERM.match(text)
    if bold:
        term = bold.group(2).strip().rstrip(":：").strip()
        rest = bold.group(3).strip()
        paren = re.match(r"^(\([^)]{1,80}\))\s*(.*)$", rest, re.S)
        if paren:
            term = f"{term} {paren.group(1)}"
            rest = paren.group(2).strip()
        rest = re.sub(r"^[:：—–-]+\s*", "", rest)
        return term, rest
    for pattern in _PLAIN_TERM:
        match = pattern.match(text)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None


def _term_card(term: str, definition: str, section: str, index: _HintIndex) -> StudyCard:
    term = _unwrap_bold(term.strip())
    if len(_plain(term)) > _MAX_TERM:
        term = term[:_MAX_TERM].rsplit(" ", 1)[0] + "…"
    # "Очередь (queue)": the translation in brackets rarely appears in the text.
    lookup = re.sub(r"\([^)]*\)", " ", term)
    timestamps = find_timestamps(definition) or index.timestamps(lookup, strict=True)
    return StudyCard(
        card_id=_card_id("t", term),
        kind="term",
        front=term,
        back=definition.strip(),
        section=section,
        timestamps=tuple(timestamps[:3]),
    )


# ======================================================================== hints
def _stem(word: str) -> str:
    return word[: max(3, min(len(word) - 1, 6))]


def _prefixes(word: str) -> set[str]:
    return {word[:length] for length in range(3, min(len(word), 6) + 1)}


@dataclass
class _Block:
    text: str
    words: set[str]
    prefixes: set[str]
    timestamps: list[int]


class _HintIndex:
    """Paragraph-level index of the lesson for hints and timestamp lookup."""

    def __init__(self, lines: list[str], excluded: set[int]) -> None:
        self.blocks: list[_Block] = []
        current: list[str] = []
        fence: str | None = None

        def flush() -> None:
            text = textwrap.dedent("\n".join(current)).strip()
            current.clear()
            if not text:
                return
            words = {word.casefold() for word in _WORD.findall(_plain(text))}
            prefixes: set[str] = set()
            for word in words:
                prefixes |= _prefixes(word)
            self.blocks.append(_Block(text, words, prefixes, find_timestamps(text)))

        for number, line in enumerate(lines):
            fence_match = _FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)[0]
                fence = marker if fence is None else (None if fence == marker else fence)
                flush()
                continue
            if fence is not None or number in excluded:
                flush()
                continue
            stripped = line.strip()
            if not stripped or _HEADING.match(line) or _HR.match(line) or is_table_delimiter(line):
                flush()
                continue
            if stripped.startswith("|"):
                # A table row on its own reads better as "cell — cell".
                flush()
                current.append(" — ".join(cell for cell in split_table_row(line) if cell))
                flush()
                continue
            if _indent(line) <= 1 and (_BULLET.match(line) or _ORDERED.match(line)):
                flush()
            current.append(line)
        flush()

    def _query(self, text: str) -> dict[str, str]:
        """Stem → original word for the meaningful words of a query."""

        stems: dict[str, str] = {}
        for word in _WORD.findall(_plain(text).casefold()):
            if len(word) < 4 or word in _STOPWORDS:
                continue
            stems.setdefault(_stem(word), word)
        return stems

    def _ranked(self, text: str, *, strict: bool = False) -> list[tuple[float, int]]:
        stems = self._query(text)
        if not stems or not self.blocks:
            return []
        total_blocks = len(self.blocks)
        weights = {}
        for stem in stems:
            frequency = sum(1 for block in self.blocks if stem in block.prefixes)
            if frequency:
                weights[stem] = math.log((total_blocks + 1) / (frequency + 0.5)) + 0.1
        if not weights:
            return []
        possible = sum(weights.values())
        needed = possible * (0.99 if strict else 0.4)
        ranked: list[tuple[float, int]] = []
        for position, block in enumerate(self.blocks):
            matched = [stem for stem in weights if stem in block.prefixes]
            score = sum(weights[stem] for stem in matched)
            if score < needed or score <= 0:
                continue
            # The exact word form only breaks ties: "стек" beats "стеках".
            score += sum(0.3 * weights[stem] for stem in matched if stems[stem] in block.words)
            ranked.append((score, position))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return ranked

    def hint(self, question: str, *, max_blocks: int = 2) -> str:
        ranked = self._ranked(question)[: max(1, max_blocks)]
        chosen = sorted(position for _, position in ranked)
        return "\n\n".join(self.blocks[position].text for position in chosen)

    def timestamps(self, text: str, *, strict: bool = False) -> list[int]:
        for _, position in self._ranked(text, strict=strict):
            if self.blocks[position].timestamps:
                return self.blocks[position].timestamps
        return []


# ====================================================================== helpers
def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_text(lines: list[str]) -> str:
    if not lines:
        return ""
    first, rest = lines[0].strip(), textwrap.dedent("\n".join(lines[1:]))
    return _join_parts(first, rest)


def _join_parts(first: str, second: str) -> str:
    first, second = first.strip(), second.strip()
    if first and second:
        return f"{first}\n{second}"
    return first or second


def _unwrap_bold(text: str) -> str:
    match = _WHOLE_BOLD.match(text)
    if match and match.group(1) not in match.group(2):
        return match.group(2).strip()
    return text


def _card_id(prefix: str, front: str) -> str:
    normalized = re.sub(r"[^\w\s]", "", _plain(front).casefold())
    normalized = " ".join(normalized.split())
    return hashlib.sha1(f"{prefix}:{normalized}".encode()).hexdigest()[:16]


def _card_rank(card: StudyCard) -> tuple[int, int]:
    return (0 if card.hint or not card.back else 1, len(card.back))
