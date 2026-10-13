"""Markdown → safe HTML for the reader, flashcards and exports.

Covers what lesson notes actually use: headings, paragraphs, nested and task
lists, block quotes (with GitHub-style callouts), fenced code, tables, rules,
emphasis, inline code, links and lecture timestamps such as ``[00:12:34]``.

Raw HTML is never passed through: every piece of text is escaped, ``<br>`` is
the only tag that is understood, and only http(s)/mailto links survive.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "RenderedHeading",
    "RenderedMarkdown",
    "find_timestamps",
    "format_clock",
    "is_table_delimiter",
    "parse_timestamp",
    "render_inline",
    "render_markdown",
    "split_table_row",
    "strip_inline_markdown",
]


@dataclass(frozen=True)
class RenderedHeading:
    level: int
    title: str
    anchor: str


@dataclass(frozen=True)
class RenderedMarkdown:
    html: str
    headings: tuple[RenderedHeading, ...]
    word_count: int


# Private-use control characters mark spots the inline pass fills in later.
# They are stripped from the input first, so text can never forge them.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HARD_BREAK = "\x01"
_TOKEN_OPEN = "\x02"
_TOKEN_CLOSE = "\x03"
_TASK_OPEN = "\x04"
_TASK_DONE = "\x05"
_TOKEN = re.compile("\x02(\\d+)\x03")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

_MAX_DEPTH = 16
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})

# ---------------------------------------------------------------- inline rules
_CODE_SPAN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)", re.S)
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~<>])")
_BR_TAG = re.compile(r"<br\s*/?>", re.I)
_AUTOLINK = re.compile(r"<((?:https?|mailto):[^\s<>]+)>", re.I)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\s*\)")
_LINK = re.compile(
    r"\[((?:[^\[\]]|\[[^\[\]]*\])+)\]"
    r"\(\s*<?([^()\s<>]*(?:\([^()\s]*\)[^()\s<>]*)*)>?(?:\s+\"[^\"]*\")?\s*\)"
)
_BARE_URL = re.compile(r"(?<![\w/@])https?://[^\s<>\"\x01-\x05]+", re.I)

_TS_CORE = r"\d{1,2}:[0-5]\d(?::[0-5]\d)?"
_TIMESTAMP = re.compile(_TS_CORE)
_TIMESTAMP_GROUP = re.compile(rf"\[\s*{_TS_CORE}(?:\s*(?:[-–—,;]|и)\s*{_TS_CORE})*\s*\]")
# Without brackets only HH:MM:SS counts: "в 10:30" or "масштаб 1:50" stay text.
_BARE_TIMESTAMP = re.compile(r"(?<![\w:.])\d{1,2}:[0-5]\d:[0-5]\d(?![\w:])")

_EMPHASIS_RULES = (
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.S), "<strong><em>", "</em></strong>"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S), "<strong>", "</strong>"),
    (re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])", re.S), "<strong>", "</strong>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S), "<del>", "</del>"),
    (re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", re.S), "<em>", "</em>"),
    (re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", re.S), "<em>", "</em>"),
)
_EMPHASIS_TAG = re.compile(r"<(/?)(strong|em|del)>")
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")

# ----------------------------------------------------------------- block rules
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_HR = re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_BULLET = re.compile(r"^( *)([-*+])(?:[ \t]+(.*)|[ \t]*)$")
_ORDERED = re.compile(r"^( *)(\d{1,9})([.)])(?:[ \t]+(.*)|[ \t]*)$")
_TABLE_DELIMITER = re.compile(r"^ *\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *$")
_TASK = re.compile(r"^\[([ xXхХ])\][ \t]+")
_CALLOUT = re.compile(r"^\[!(note|tip|important|warning|caution)\][ \t]*(.*)$", re.I)
_CALLOUT_TITLES = {
    "note": "Заметка",
    "tip": "Совет",
    "important": "Важно",
    "warning": "Внимание",
    "caution": "Осторожно",
}


# ======================================================================= public
def render_markdown(text: str, *, timestamp_tag: str = "button") -> RenderedMarkdown:
    """Render lesson Markdown into escaped HTML plus its outline and word count."""

    source = _HTML_COMMENT.sub("", _clean(text))
    lines = source.expandtabs(4).split("\n")
    renderer = _BlockRenderer(timestamp_tag)
    body = "\n".join(renderer.render(lines))
    words = len(_WORD.findall(strip_inline_markdown(source)))
    return RenderedMarkdown(html=body, headings=tuple(renderer.headings), word_count=words)


def render_inline(text: str, *, timestamp_tag: str = "button") -> str:
    """Render one line of Markdown (emphasis, code, links, timestamps) to HTML."""

    return _Inline(timestamp_tag).render(_clean(text).strip())


def strip_inline_markdown(text: str) -> str:
    """Plain text of a Markdown fragment: markers removed, link labels kept."""

    text = _CODE_SPAN.sub(lambda match: match.group(2).strip(), text)
    text = _IMAGE.sub(lambda match: match.group(1), text)
    text = _LINK.sub(lambda match: match.group(1), text)
    text = _AUTOLINK.sub(lambda match: match.group(1), text)
    text = _BR_TAG.sub(" ", text)
    for pattern, _, _ in _EMPHASIS_RULES:
        text = pattern.sub(lambda match: match.group(1), text)
    return _ESCAPE.sub(lambda match: match.group(1), text)


def parse_timestamp(raw: str) -> int | None:
    """Seconds for ``MM:SS`` or ``HH:MM:SS`` (brackets allowed), else ``None``."""

    parts = raw.strip().strip("[]()").strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if any(number >= 60 for number in numbers[1:]):
        return None
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def format_clock(seconds: int) -> str:
    """``754`` → ``12:34``; ``3723`` → ``1:02:03``."""

    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def find_timestamps(text: str) -> list[int]:
    """Seconds of the lecture timestamps in a fragment, in order, without repeats.

    Uses the same rules as rendering: anything in brackets (``[12:34]``,
    ``[00:01:00–00:02:30]``) plus bare ``HH:MM:SS``.
    """

    found: list[tuple[int, int]] = []
    spans: list[tuple[int, int]] = []
    for group in _TIMESTAMP_GROUP.finditer(text):
        spans.append(group.span())
        for item in _TIMESTAMP.finditer(group.group(0)):
            seconds = parse_timestamp(item.group(0))
            if seconds is not None:
                found.append((group.start() + item.start(), seconds))
    for item in _BARE_TIMESTAMP.finditer(text):
        if any(start <= item.start() < end for start, end in spans):
            continue
        seconds = parse_timestamp(item.group(0))
        if seconds is not None:
            found.append((item.start(), seconds))
    ordered: list[int] = []
    for _, seconds in sorted(found):
        if seconds not in ordered:
            ordered.append(seconds)
    return ordered


def is_table_delimiter(line: str) -> bool:
    """``|---|:-:|`` — the line under a table header."""

    return "|" in line and bool(_TABLE_DELIMITER.match(line))


# ====================================================================== helpers
def _clean(text: str) -> str:
    return _CONTROL_CHARS.sub("", str(text or "").replace("\r\n", "\n").replace("\r", "\n"))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _safe_href(url: str) -> str | None:
    url = url.strip().strip("<>")
    if not url or _TOKEN_OPEN in url or any(char.isspace() for char in url):
        return None
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return None
    if scheme not in _SAFE_SCHEMES:
        return None
    return html.escape(url, quote=True)


def _balanced(fragment: str) -> bool:
    stack: list[str] = []
    for match in _EMPHASIS_TAG.finditer(fragment):
        if match.group(1):
            if not stack or stack.pop() != match.group(2):
                return False
        else:
            stack.append(match.group(2))
    return not stack


def _apply_emphasis(text: str) -> str:
    for pattern, opening, closing in _EMPHASIS_RULES:

        def replace(match: re.Match[str], opening: str = opening, closing: str = closing) -> str:
            inner = match.group(1)
            if not _balanced(inner):
                return match.group(0)
            return f"{opening}{inner}{closing}"

        text = pattern.sub(replace, text)
    return text


class _Inline:
    """One inline pass. Finished fragments are stashed behind placeholders so
    later rules (escaping, emphasis) cannot touch their markup."""

    def __init__(self, timestamp_tag: str) -> None:
        self.timestamp_tag = timestamp_tag
        self.tokens: list[str] = []

    def render(self, text: str) -> str:
        return self._restore(self._format(text, nested=False))

    def _stash(self, fragment: str) -> str:
        self.tokens.append(fragment)
        return f"{_TOKEN_OPEN}{len(self.tokens) - 1}{_TOKEN_CLOSE}"

    def _restore(self, text: str) -> str:
        for _ in range(_MAX_DEPTH):
            if _TOKEN_OPEN not in text:
                break
            text = _TOKEN.sub(lambda match: self.tokens[int(match.group(1))], text)
        return text

    def _format(self, text: str, *, nested: bool) -> str:
        text = _CODE_SPAN.sub(self._code, text)
        text = _ESCAPE.sub(lambda match: self._stash(html.escape(match.group(1))), text)
        text = _BR_TAG.sub(_HARD_BREAK, text)
        if not nested:
            text = _AUTOLINK.sub(self._autolink, text)
        text = _IMAGE.sub(self._image, text)
        if not nested:
            text = _LINK.sub(self._link, text)
            text = _TIMESTAMP_GROUP.sub(self._timestamp_group, text)
            text = _BARE_URL.sub(self._bare_url, text)
            text = _BARE_TIMESTAMP.sub(
                lambda match: self._stash(self._timestamp(match.group(0))), text
            )
        # Entities such as "&lt;" or "&nbsp;" are decoded and escaped again.
        # html.unescape drops references to control characters, so this cannot
        # forge a placeholder.
        text = _apply_emphasis(html.escape(html.unescape(text), quote=False))
        text = text.replace("\n", _HARD_BREAK).replace(_HARD_BREAK, "<br>\n")
        text = text.replace(_TASK_OPEN, '<span class="check">☐</span> ')
        return text.replace(_TASK_DONE, '<span class="check">☑</span> ')

    def _code(self, match: re.Match[str]) -> str:
        content = match.group(2).replace("\n", " ")
        if content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]
        return self._stash(f"<code>{html.escape(content, quote=False)}</code>")

    def _anchor(self, href: str, label_html: str) -> str:
        return (
            f'<a class="ext" href="{href}" target="_blank" rel="noopener noreferrer">'
            f"{label_html}</a>"
        )

    def _autolink(self, match: re.Match[str]) -> str:
        url = match.group(1)
        href = _safe_href(url)
        label = html.escape(url, quote=False)
        return self._stash(self._anchor(href, label) if href else label)

    def _image(self, match: re.Match[str]) -> str:
        alt = match.group(1).strip()
        label = f"[изображение: {alt}]" if alt else "[изображение]"
        return self._stash(f'<span class="img-alt">{html.escape(label, quote=False)}</span>')

    def _link(self, match: re.Match[str]) -> str:
        label_html = self._format(match.group(1), nested=True)
        href = _safe_href(match.group(2))
        return self._stash(self._anchor(href, label_html) if href else label_html)

    def _bare_url(self, match: re.Match[str]) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in ".,;:!?»\"')]*_~":
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            trailing = url[-1] + trailing
            url = url[:-1]
        href = _safe_href(url)
        if href is None:
            return match.group(0)
        return self._stash(self._anchor(href, html.escape(url, quote=False))) + trailing

    def _timestamp(self, raw: str) -> str:
        seconds = parse_timestamp(raw)
        if seconds is None:
            return html.escape(raw, quote=False)
        label = format_clock(seconds)
        if self.timestamp_tag != "button":
            return f'<span class="ts">{label}</span>'
        return (
            f'<button type="button" class="ts" data-t="{seconds}" '
            f'title="Включить запись с {label}">{label}</button>'
        )

    def _timestamp_group(self, match: re.Match[str]) -> str:
        inner = match.group(0)[1:-1]
        parts: list[str] = []
        position = 0
        for found in _TIMESTAMP.finditer(inner):
            separator = inner[position : found.start()].strip()
            if parts:
                if separator in {",", ";"}:
                    parts.append(f"{separator} ")
                elif separator == "и":
                    parts.append(" и ")
                else:
                    parts.append("–")
            parts.append(self._timestamp(found.group(0)))
            position = found.end()
        return self._stash("".join(parts))


@dataclass(frozen=True)
class _Marker:
    indent: int
    ordered: bool
    number: int
    content_offset: int
    text: str


def _list_marker(line: str) -> _Marker | None:
    if _HR.match(line):
        return None
    match = _BULLET.match(line)
    ordered = False
    if match is None:
        match = _ORDERED.match(line)
        ordered = True
        if match is None:
            return None
    indent = len(match.group(1))
    marker_end = match.end(3) if ordered else match.end(2)
    rest = line[marker_end:]
    gap = len(rest) - len(rest.lstrip(" "))
    text = (match.group(4) if ordered else match.group(3)) or ""
    return _Marker(
        indent=indent,
        ordered=ordered,
        number=int(match.group(2)) if ordered else 1,
        content_offset=marker_end + (gap if text and 1 <= gap <= 4 else 1),
        text=text.strip(),
    )


def split_table_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells: list[str] = []
    current: list[str] = []
    code_run = 0
    index = 0
    while index < len(row):
        char = row[index]
        if char == "\\" and index + 1 < len(row) and row[index + 1] == "|":
            current.append("|" if code_run else "\\|")
            index += 2
            continue
        if char == "`":
            end = index
            while end < len(row) and row[end] == "`":
                end += 1
            run = end - index
            if not code_run:
                code_run = run
            elif run == code_run:
                code_run = 0
            current.append(row[index:end])
            index = end
            continue
        if char == "|" and not code_run:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _alignment(cell: str) -> str:
    cell = cell.strip()
    if cell.startswith(":") and cell.endswith(":"):
        return "center"
    if cell.endswith(":"):
        return "right"
    if cell.startswith(":"):
        return "left"
    return ""


class _BlockRenderer:
    def __init__(self, timestamp_tag: str) -> None:
        self.timestamp_tag = timestamp_tag
        self.headings: list[RenderedHeading] = []

    def inline(self, text: str) -> str:
        return _Inline(self.timestamp_tag).render(text)

    # ---------------------------------------------------------------- blocks
    def render(self, lines: list[str], *, tight: bool = False, depth: int = 0) -> list[str]:
        out: list[str] = []
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                body = self.inline("\n".join(paragraph))
                out.append(body if tight else f"<p>{body}</p>")
                paragraph.clear()

        if depth >= _MAX_DEPTH:
            paragraph.extend(line.strip() for line in lines if line.strip())
            flush()
            return out

        index = 0
        total = len(lines)
        while index < total:
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                flush()
                index += 1
                continue

            if _FENCE.match(line):
                flush()
                block, index = self._fence(lines, index)
                out.append(block)
                continue

            heading = _HEADING.match(line)
            if heading:
                flush()
                title = heading.group(2).strip()
                if title:
                    out.append(self._heading(len(heading.group(1)), title))
                index += 1
                continue

            if _HR.match(line):
                flush()
                out.append("<hr>")
                index += 1
                continue

            if _QUOTE.match(line):
                flush()
                block, index = self._quote(lines, index, depth)
                out.append(block)
                continue

            marker = _list_marker(line)
            if marker is not None and (not paragraph or not marker.ordered or marker.number == 1):
                flush()
                block, index = self._list(lines, index, depth)
                out.append(block)
                continue

            if self._is_table_start(lines, index):
                flush()
                block, index = self._table(lines, index)
                out.append(block)
                continue

            if stripped.endswith("\\") and not stripped.endswith("\\\\"):
                stripped = stripped[:-1].rstrip()
            paragraph.append(stripped)
            index += 1

        flush()
        return out

    def _starts_block(self, lines: list[str], index: int) -> bool:
        line = lines[index]
        return bool(
            _FENCE.match(line)
            or _HEADING.match(line)
            or _HR.match(line)
            or _QUOTE.match(line)
            or self._is_table_start(lines, index)
        )

    def _heading(self, level: int, title: str) -> str:
        anchor = f"sec-{len(self.headings) + 1}"
        plain = " ".join(strip_inline_markdown(_TIMESTAMP_GROUP.sub(" ", title)).split())
        self.headings.append(
            RenderedHeading(level=level, title=html.unescape(plain) or title, anchor=anchor)
        )
        return f'<h{level} id="{anchor}">{self.inline(title)}</h{level}>'

    def _fence(self, lines: list[str], index: int) -> tuple[str, int]:
        match = _FENCE.match(lines[index])
        assert match is not None
        indent = len(match.group(1))
        fence = match.group(2)
        language = re.sub(r"[^\w+#-]", "", match.group(3))[:24]
        body: list[str] = []
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if candidate and set(candidate) == {fence[0]} and len(candidate) >= len(fence):
                index += 1
                break
            line = lines[index]
            body.append(line[min(indent, _indent(line)) :])
            index += 1
        css = f' class="lang-{html.escape(language, quote=True)}"' if language else ""
        code = html.escape("\n".join(body), quote=False)
        return f"<pre><code{css}>{code}</code></pre>", index

    def _quote(self, lines: list[str], index: int, depth: int) -> tuple[str, int]:
        inner: list[str] = []
        while index < len(lines):
            match = _QUOTE.match(lines[index])
            if match is None:
                break
            inner.append(match.group(1))
            index += 1
        css = ""
        callout = _CALLOUT.match(inner[0].strip()) if inner else None
        if callout:
            kind = callout.group(1).lower()
            title = f"**{_CALLOUT_TITLES[kind]}.**"
            rest = callout.group(2).strip()
            inner[0] = f"{title} {rest}" if rest else title
            css = f' class="callout callout-{kind}"'
        body = "\n".join(self.render(inner, depth=depth + 1))
        return f"<blockquote{css}>\n{body}\n</blockquote>", index

    def _list(self, lines: list[str], index: int, depth: int) -> tuple[str, int]:
        first = _list_marker(lines[index])
        assert first is not None
        base = first.indent
        items: list[list[str]] = []
        offsets: list[int] = []
        loose = False
        total = len(lines)
        while index < total:
            line = lines[index]
            if not line.strip():
                following = index
                while following < total and not lines[following].strip():
                    following += 1
                if following >= total:
                    index = following
                    break
                marker = _list_marker(lines[following])
                if marker is not None and marker.indent <= base:
                    if marker.ordered != first.ordered:
                        break
                    loose = True
                    index = following
                    continue
                if _indent(lines[following]) > base:
                    items[-1].extend([""] * (following - index))
                    loose = True
                    index = following
                    continue
                break

            marker = _list_marker(line)
            if marker is not None and marker.indent <= base:
                if marker.ordered != first.ordered:
                    break
                items.append([marker.text])
                offsets.append(marker.content_offset)
                index += 1
                continue

            indent = _indent(line)
            if indent > base:
                items[-1].append(line[min(indent, offsets[-1]) :])
                index += 1
                continue

            previous = items[-1][-1] if items[-1] else ""
            if previous.strip() and not self._starts_block(lines, index):
                # Lazy continuation: "1. Вопрос?\nОтвет: …" keeps the answer in the item.
                items[-1].append(line.strip())
                index += 1
                continue
            break

        tag = "ol" if first.ordered else "ul"
        start = f' start="{first.number}"' if first.ordered and first.number != 1 else ""
        parts = [f"<{tag}{start}>"]
        for body in items:
            css = ""
            task = _TASK.match(body[0]) if body else None
            if task:
                sentinel = _TASK_OPEN if task.group(1) == " " else _TASK_DONE
                body = [sentinel + body[0][task.end() :], *body[1:]]
                css = ' class="task"'
            inner = "\n".join(self.render(body, tight=not loose, depth=depth + 1))
            parts.append(f"<li{css}>{inner}</li>")
        parts.append(f"</{tag}>")
        return "\n".join(parts), index

    def _is_table_start(self, lines: list[str], index: int) -> bool:
        if index + 1 >= len(lines):
            return False
        header, delimiter = lines[index], lines[index + 1]
        if "|" not in header or not is_table_delimiter(delimiter):
            return False
        return len(split_table_row(header)) == len(split_table_row(delimiter))

    def _table(self, lines: list[str], index: int) -> tuple[str, int]:
        header = split_table_row(lines[index])
        aligns = [_alignment(cell) for cell in split_table_row(lines[index + 1])]
        index += 2
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            cells = split_table_row(lines[index])
            rows.append((cells + [""] * len(header))[: len(header)])
            index += 1

        def cell(tag: str, text: str, align: str) -> str:
            css = f' class="al-{align}"' if align else ""
            return f"<{tag}{css}>{self.inline(text)}</{tag}>"

        parts = ['<div class="table-wrap"><table>', "<thead><tr>"]
        parts.extend(cell("th", text, align) for text, align in zip(header, aligns))
        parts.append("</tr></thead>")
        if rows:
            parts.append("<tbody>")
            for row in rows:
                parts.append(
                    "<tr>"
                    + "".join(cell("td", text, align) for text, align in zip(row, aligns))
                    + "</tr>"
                )
            parts.append("</tbody>")
        parts.append("</table></div>")
        return "\n".join(parts), index
