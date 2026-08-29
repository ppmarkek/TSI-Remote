"""Safe parsing, sanitization, table of contents extraction, and timestamp routing for lesson notes."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_TIMESTAMP_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class TocEntry:
    level: int
    title: str
    line_number: int


@dataclass(frozen=True)
class LessonTimestamp:
    raw_str: str
    total_seconds: float
    line_number: int


def sanitize_markdown_text(text: str) -> str:
    """Strip raw HTML tags and dangerous protocols to ensure safe desktop rendering."""
    no_html = _HTML_TAG_PATTERN.sub("", text)
    safe = html.escape(no_html)
    # Remove javascript: and data: URIs
    safe = re.sub(r"(?i)javascript:\s*", "", safe)
    safe = re.sub(r"(?i)data:\s*", "", safe)
    return safe


def extract_table_of_contents(markdown_text: str) -> list[TocEntry]:
    """Extract headings (#, ##, ###) for fast lesson navigation."""
    toc: list[TocEntry] = []
    lines = markdown_text.splitlines()
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                if title:
                    toc.append(TocEntry(level=level, title=title, line_number=idx))
    return toc


def extract_timestamps(markdown_text: str) -> list[LessonTimestamp]:
    """Extract playback timestamps (MM:SS or HH:MM:SS) for media synchronization."""
    timestamps: list[LessonTimestamp] = []
    lines = markdown_text.splitlines()
    for idx, line in enumerate(lines, start=1):
        for match in _TIMESTAMP_PATTERN.finditer(line):
            part1 = int(match.group(1))
            part2 = int(match.group(2))
            part3 = match.group(3)
            if part3 is not None:
                # HH:MM:SS
                total = part1 * 3600 + part2 * 60 + int(part3)
            else:
                # MM:SS
                total = part1 * 60 + part2
            timestamps.append(
                LessonTimestamp(
                    raw_str=match.group(0),
                    total_seconds=float(total),
                    line_number=idx,
                )
            )
    return timestamps
