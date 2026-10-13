"""Safe HTML and portable PDF export for generated lesson notes."""

from __future__ import annotations

import html
import io
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_text
from .markdown_html import (
    is_table_delimiter,
    render_markdown,
    split_table_row,
    strip_inline_markdown,
)
from .markdown_reader import extract_table_of_contents, sanitize_markdown_text

_PDF_BULLET = re.compile(r"^[-*+]\s+(?:\[[ xX]\]\s+)?")
_PDF_NUMBERED = re.compile(r"^(\d{1,4})[.)]\s+")


_EXPORT_STYLES = """
    :root {
      --ink: #1D2129;
      --muted: #5C6370;
      --line: #E3E5EA;
      --soft: #F5F6F8;
      --accent: #4F5BD5;
      --accent-soft: #EEF0FC;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #FFFFFF;
      color: var(--ink);
      font: 16px/1.65 "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, "Helvetica Neue",
        Arial, sans-serif;
    }
    .page { max-width: 720px; margin: 0 auto; padding: 48px 24px 72px; }
    .doc-title { font-size: 30px; line-height: 1.25; margin: 0 0 6px; letter-spacing: -0.01em; }
    .doc-meta { color: var(--muted); font-size: 14px; margin: 0 0 28px; }
    .toc {
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 16px 20px;
      margin: 0 0 36px;
    }
    .toc h2 {
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
      margin: 0 0 8px;
    }
    .toc ol { list-style: none; margin: 0; padding: 0; }
    .toc li { margin: 2px 0; }
    .toc a { color: var(--ink); text-decoration: none; }
    .toc a:hover { color: var(--accent); }
    .toc .level-2 { padding-left: 16px; }
    .toc .level-3, .toc .level-4, .toc .level-5, .toc .level-6 {
      padding-left: 32px;
      font-size: 14px;
      color: var(--muted);
    }
    .toc p { margin: 0; color: var(--muted); }
    main h1, main h2, main h3, main h4, main h5, main h6 {
      line-height: 1.3;
      margin: 1.8em 0 0.6em;
    }
    main h1 { font-size: 26px; }
    main h2 { font-size: 21px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
    main h3 { font-size: 18px; }
    main h4, main h5, main h6 { font-size: 16px; }
    main > :first-child { margin-top: 0; }
    p, ul, ol, blockquote, pre, .table-wrap { margin: 0 0 1em; }
    ul, ol { padding-left: 24px; }
    li { margin: 0.25em 0; }
    li.task { list-style: none; margin-left: -20px; }
    .check { color: var(--accent); }
    a { color: var(--accent); }
    strong { font-weight: 650; }
    code {
      font-family: "Cascadia Mono", Consolas, "SFMono-Regular", Menlo, monospace;
      font-size: 0.9em;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 1px 5px;
    }
    pre {
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 14px 16px;
      overflow-x: auto;
      white-space: pre-wrap;
    }
    pre code { background: none; border: 0; padding: 0; font-size: 14px; }
    blockquote {
      border-left: 3px solid var(--line);
      padding: 4px 0 4px 16px;
      color: var(--muted);
    }
    blockquote.callout {
      border-left-color: var(--accent);
      background: var(--accent-soft);
      border-radius: 0 10px 10px 0;
      padding: 10px 16px;
      color: var(--ink);
    }
    blockquote.callout-tip { border-left-color: #1E8E5A; background: #E7F6EE; }
    blockquote.callout-warning { border-left-color: #B26B00; background: #FDF3E1; }
    blockquote.callout-caution { border-left-color: #D14343; background: #FCEBEB; }
    blockquote > :last-child { margin-bottom: 0; }
    hr { border: 0; border-top: 1px solid var(--line); margin: 2em 0; }
    .table-wrap { overflow-x: auto; }
    table { border-collapse: collapse; width: 100%; font-size: 15px; }
    th, td { border: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }
    th { background: var(--soft); font-weight: 600; }
    .al-center { text-align: center; }
    .al-right { text-align: right; }
    .ts {
      display: inline-block;
      font-size: 0.85em;
      font-variant-numeric: tabular-nums;
      color: var(--accent);
      background: var(--accent-soft);
      border-radius: 999px;
      padding: 0 8px;
    }
    .img-alt { color: var(--muted); font-style: italic; }
    @media print {
      .page { max-width: none; padding: 0; }
      .toc { break-after: page; }
      h1, h2, h3, h4 { break-after: avoid; }
      pre, table, blockquote { break-inside: avoid; }
      a { color: inherit; text-decoration: none; }
    }
"""


def render_lesson_html(title: str, markdown_content: str) -> str:
    """Render a standalone, escaped HTML document with a linked table of contents."""

    clean_title = "".join(ch for ch in title.strip() if ch.isprintable()) or "Конспект"
    rendered = render_markdown(markdown_content, timestamp_tag="span")
    toc_items = "\n".join(
        f'      <li class="level-{heading.level}"><a href="#{heading.anchor}">'
        f"{html.escape(heading.title)}</a></li>"
        for heading in rendered.headings
    )
    toc_html = f"<ol>\n{toc_items}\n    </ol>" if toc_items else "<p>Разделы не найдены.</p>"
    minutes = max(1, round(rendered.word_count / 180)) if rendered.word_count else 0
    meta = (
        f'\n  <p class="doc-meta">{rendered.word_count} слов · около {minutes} мин чтения</p>'
        if minutes
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(clean_title)}</title>
  <style>{_EXPORT_STYLES}  </style>
</head>
<body>
  <div class="page">
  <h1 class="doc-title">{html.escape(clean_title)}</h1>{meta}
  <nav class="toc" aria-label="Оглавление">
    <h2>Оглавление</h2>
    {toc_html}
  </nav>
  <main>
{rendered.html}
  </main>
  </div>
</body>
</html>
"""


def export_lesson_to_html_file(
    title: str,
    markdown_content: str,
    output_path: Path,
) -> Path:
    """Save the rendered HTML lesson atomically."""

    rendered = render_lesson_html(title, markdown_content)
    try:
        atomic_write_text(output_path, rendered, encoding="utf-8")
    except (AtomicIOError, OSError) as exc:
        raise RuntimeError(f"Не удалось экспортировать конспект в HTML: {exc}") from exc
    return output_path


def export_lesson_to_pdf_file(
    title: str,
    markdown_content: str,
    output_path: Path,
) -> Path:
    """Export a lesson to a PDF that displays Cyrillic without viewer fonts.

    WeasyPrint and wkhtmltopdf remain preferred when installed. The bundled
    fallback rasterizes each page with a verified Cyrillic-capable system font
    and embeds the page pixels in the PDF. Unlike the previous Identity-H /
    Helvetica construction, the resulting document does not rely on font
    substitution by the PDF viewer.
    """

    rendered = render_lesson_html(title, markdown_content)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from weasyprint import HTML  # type: ignore[import-not-found]

        temporary_pdf = output_path.with_name(f".{output_path.name}.weasy.tmp")
        try:
            HTML(string=rendered, base_url=str(output_path.parent)).write_pdf(str(temporary_pdf))
            if temporary_pdf.is_file() and temporary_pdf.stat().st_size > 0:
                os.replace(temporary_pdf, output_path)
                return output_path
        finally:
            temporary_pdf.unlink(missing_ok=True)
    except ImportError:
        pass
    except Exception:
        # Fall through to the next local renderer. User material remains intact.
        pass

    converter = shutil.which("wkhtmltopdf")
    if converter:
        temporary_html = output_path.with_name(f".{output_path.name}.html.tmp")
        temporary_pdf = output_path.with_name(f".{output_path.name}.wkhtml.tmp")
        try:
            atomic_write_text(temporary_html, rendered, encoding="utf-8")
            result = subprocess.run(
                [converter, "--quiet", str(temporary_html), str(temporary_pdf)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if result.returncode == 0 and temporary_pdf.is_file() and temporary_pdf.stat().st_size:
                os.replace(temporary_pdf, output_path)
                return output_path
        except (AtomicIOError, OSError, subprocess.TimeoutExpired):
            pass
        finally:
            temporary_html.unlink(missing_ok=True)
            temporary_pdf.unlink(missing_ok=True)

    try:
        _atomic_write_bytes(output_path, _render_pure_python_pdf(title, markdown_content))
    except Exception as exc:
        raise RuntimeError(f"Не удалось экспортировать конспект в PDF: {exc}") from exc
    return output_path


def _render_pure_python_pdf(title: str, markdown_content: str) -> bytes:
    """Render a multi-page image PDF with visible Unicode/Cyrillic text."""

    pages = _render_pdf_page_images(title, markdown_content)
    buffer = io.BytesIO()
    first, *remaining = pages
    first.save(
        buffer,
        format="PDF",
        save_all=True,
        append_images=remaining,
        resolution=144.0,
        quality=95,
    )
    payload = buffer.getvalue()
    if not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF"):
        raise RuntimeError("Встроенный PDF-рендерер вернул повреждённый документ.")
    return payload


def _render_pdf_page_images(title: str, markdown_content: str) -> list[Any]:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(
            "Для автономного PDF требуется Pillow, входящий в официальный пакет Konspekt."
        ) from exc

    clean_title = html.unescape(sanitize_markdown_text(title.strip())) or "Конспект"
    fonts = {
        "title": _load_pdf_font(48, bold=True),
        "h1": _load_pdf_font(38, bold=True),
        "h2": _load_pdf_font(32, bold=True),
        "h3": _load_pdf_font(28, bold=True),
        "body": _load_pdf_font(24),
        "small": _load_pdf_font(19),
    }

    page_width, page_height = 1240, 1754  # A4 at approximately 150 DPI.
    margin_x, margin_top, margin_bottom = 92, 96, 92
    text_width = page_width - 2 * margin_x
    ink = (29, 33, 41)
    muted = (92, 99, 112)
    accent = (79, 91, 213)
    paper = (255, 255, 255)

    pages: list[Any] = []
    image: Any
    draw: Any
    cursor_y = 0

    def new_page(*, continuation: bool = False) -> None:
        nonlocal image, draw, cursor_y
        image = Image.new("RGB", (page_width, page_height), paper)
        draw = ImageDraw.Draw(image)
        cursor_y = margin_top
        if continuation:
            header = clean_title[:80]
            draw.text((margin_x, 48), header, font=fonts["small"], fill=muted)
            draw.line((margin_x, 78, page_width - margin_x, 78), fill=(220, 226, 223), width=2)
            cursor_y = 102
        pages.append(image)

    new_page()

    def draw_block(
        text: str, kind: str, *, indent: int = 0, gap: int = 12, bar: bool = False
    ) -> None:
        nonlocal cursor_y
        font = fonts[kind]
        fill = accent if kind in {"title", "h1", "h2"} else ink
        line_height = _font_line_height(font) + (10 if kind in {"title", "h1"} else 7)
        available_width = max(120, text_width - indent)
        lines = _wrap_for_width(draw, text, font, available_width)
        block_height = max(line_height, len(lines) * line_height) + gap
        if cursor_y + block_height > page_height - margin_bottom:
            new_page(continuation=True)
        top = cursor_y
        for line in lines:
            draw.text((margin_x + indent, cursor_y), line, font=font, fill=fill)
            cursor_y += line_height
        if bar:
            # Quotes get a drawn rule: box-drawing glyphs are missing from many fonts.
            x = margin_x + indent - 16
            draw.line((x, top + 4, x, cursor_y - 4), fill=accent, width=4)
        cursor_y += gap

    draw_block(clean_title, "title")

    toc = extract_table_of_contents(markdown_content)
    if toc:
        draw_block("Оглавление", "h2")
        for entry in toc:
            indent = max(0, min(3, entry.level - 1)) * 28
            draw_block(f"• {strip_inline_markdown(entry.title)}", "small", indent=indent)
        cursor_y += 18

    in_code = False
    for raw_line in markdown_content.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith(("```", "~~~")):
            in_code = not in_code
            continue
        line = html.unescape(sanitize_markdown_text(stripped))
        if not line:
            cursor_y += 10
            continue
        if in_code:
            leading = len(raw_line) - len(raw_line.lstrip(" "))
            draw_block(line, "small", indent=24 + min(leading, 24) * 10, gap=2)
            continue
        if is_table_delimiter(stripped):
            continue
        if line.startswith("#"):
            prefix, separator, heading = line.partition(" ")
            if separator and set(prefix) == {"#"}:
                level = len(prefix)
                draw_block(
                    strip_inline_markdown(heading.strip()),
                    "h1" if level == 1 else "h2" if level == 2 else "h3",
                )
                continue
        indent = min(4, (len(raw_line) - len(raw_line.lstrip())) // 2) * 24
        if line.startswith("|") and line.count("|") >= 2:
            cells = [strip_inline_markdown(cell) for cell in split_table_row(line)]
            draw_block("  ·  ".join(cell for cell in cells if cell), "body", indent=indent)
        elif bullet := _PDF_BULLET.match(line):
            rest = strip_inline_markdown(line[bullet.end() :])
            draw_block(f"• {rest}", "body", indent=indent + 24)
        elif numbered := _PDF_NUMBERED.match(line):
            rest = strip_inline_markdown(line[numbered.end() :])
            draw_block(f"{numbered.group(1)}. {rest}", "body", indent=indent + 24)
        elif line.startswith(">"):
            quote = strip_inline_markdown(line.lstrip("> ").strip())
            if not quote.startswith("[!"):
                draw_block(quote, "body", indent=indent + 28, bar=True)
        else:
            draw_block(strip_inline_markdown(line), "body", indent=indent)

    total_pages = len(pages)
    for index, page in enumerate(pages, start=1):
        page_draw = ImageDraw.Draw(page)
        footer = f"Страница {index} из {total_pages}"
        footer_width = page_draw.textlength(footer, font=fonts["small"])
        page_draw.text(
            ((page_width - footer_width) / 2, page_height - 58),
            footer,
            font=fonts["small"],
            fill=muted,
        )
    return pages


def _load_pdf_font(size: int, *, bold: bool = False) -> Any:
    try:
        from PIL import ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow недоступен для PDF-экспорта.") from exc

    for candidate in _pdf_font_candidates(bold=bold):
        try:
            font = ImageFont.truetype(str(candidate), size=size)
        except (OSError, ValueError):
            continue
        if _font_supports_cyrillic(font):
            return font
    raise RuntimeError(
        "Не найден системный шрифт с поддержкой кириллицы. "
        "Установи Segoe UI, Arial, Helvetica или DejaVu Sans и повтори экспорт."
    )


def _pdf_font_candidates(*, bold: bool) -> tuple[Path | str, ...]:
    configured = os.environ.get("KONSPEKT_PDF_FONT", "").strip()
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    if bold:
        names: tuple[Path | str, ...] = (
            windows / "seguisb.ttf",
            windows / "segoeuib.ttf",
            windows / "arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "Arial Bold.ttf",
        )
    else:
        names = (
            windows / "segoeui.ttf",
            windows / "arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "DejaVuSans.ttf",
            "Arial.ttf",
        )
    return ((Path(configured).expanduser(),) if configured else ()) + names


def _font_supports_cyrillic(font: Any) -> bool:
    signatures: set[tuple[Any, int]] = set()
    for character in "АБЯабя":
        try:
            bbox = font.getbbox(character)
            mask = bytes(font.getmask(character))
        except Exception:
            return False
        if not bbox or not mask:
            return False
        signatures.add((bbox, hash(mask)))
    # Missing glyphs normally collapse to one identical tofu box.
    return len(signatures) >= 5


def _font_line_height(font: Any) -> int:
    bbox = font.getbbox("AgЙ")
    return max(1, int(bbox[3] - bbox[1]))


def _wrap_for_width(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        lines.extend(_split_long_line(draw, current, font, max_width))
        current = word
    lines.extend(_split_long_line(draw, current, font, max_width))
    return lines


def _split_long_line(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    if draw.textlength(text, font=font) <= max_width:
        return [text]
    output: list[str] = []
    current = ""
    for character in text:
        candidate = current + character
        if current and draw.textlength(candidate, font=font) > max_width:
            output.append(current)
            current = character
        else:
            current = candidate
    if current:
        output.append(current)
    return output or [text]


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.pdf.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
