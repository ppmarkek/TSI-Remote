"""Safe HTML and portable PDF export for generated lesson notes."""

from __future__ import annotations

import html
import io
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_text
from .markdown_reader import extract_table_of_contents, sanitize_markdown_text


def render_lesson_html(title: str, markdown_content: str) -> str:
    """Render a standalone, escaped HTML document with a table of contents."""

    clean_title = sanitize_markdown_text(title.strip())
    toc = extract_table_of_contents(markdown_content)
    toc_items = "\n".join(
        f'      <li class="level-{min(6, max(1, entry.level))}">{html.escape(entry.title)}</li>'
        for entry in toc
    )
    toc_html = f"<ul>\n{toc_items}\n    </ul>" if toc_items else "<p>Разделы не найдены.</p>"

    body_parts: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            body_parts.append("</ul>")
            list_open = False

    for raw_line in markdown_content.splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line.startswith("#"):
            close_list()
            prefix, separator, heading = line.partition(" ")
            level = min(6, len(prefix)) if separator and set(prefix) == {"#"} else 0
            if level:
                body_parts.append(f"<h{level}>{html.escape(heading.strip())}</h{level}>")
                continue
        if line.startswith(("- ", "* ")):
            if not list_open:
                body_parts.append("<ul>")
                list_open = True
            body_parts.append(f"<li>{html.escape(line[2:].strip())}</li>")
        else:
            close_list()
            body_parts.append(f"<p>{html.escape(line)}</p>")
    close_list()

    body_html = "\n    ".join(body_parts)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(clean_title)}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.6;
      color: #17211D;
      max-width: 860px;
      margin: 40px auto;
      padding: 0 24px;
    }}
    h1, h2, h3 {{ color: #176B45; }}
    pre, code {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      background: #F3F6F4;
      padding: 2px 6px;
      border-radius: 4px;
    }}
    .toc {{
      background: #F7FAF8;
      padding: 18px 24px;
      border-radius: 8px;
      border: 1px solid #DDE5E0;
      margin-bottom: 32px;
    }}
    .toc .level-2 {{ margin-left: 18px; }}
    .toc .level-3, .toc .level-4, .toc .level-5, .toc .level-6 {{ margin-left: 36px; }}
    ul {{ padding-left: 24px; }}
    @media print {{
      body {{ margin: 0; max-width: none; }}
      .toc {{ break-after: page; }}
    }}
  </style>
</head>
<body>
  <h1>{html.escape(clean_title)}</h1>
  <nav class="toc" aria-label="Оглавление">
    <h2>Оглавление</h2>
    {toc_html}
  </nav>
  <main>
    {body_html}
  </main>
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
    ink = (23, 33, 36)
    muted = (78, 92, 96)
    accent = (23, 107, 69)
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

    def draw_block(text: str, kind: str, *, indent: int = 0) -> None:
        nonlocal cursor_y
        font = fonts[kind]
        fill = accent if kind in {"title", "h1", "h2"} else ink
        line_height = _font_line_height(font) + (10 if kind in {"title", "h1"} else 7)
        available_width = max(120, text_width - indent)
        lines = _wrap_for_width(draw, text, font, available_width)
        block_height = max(line_height, len(lines) * line_height) + 12
        if cursor_y + block_height > page_height - margin_bottom:
            new_page(continuation=True)
        for line in lines:
            draw.text((margin_x + indent, cursor_y), line, font=font, fill=fill)
            cursor_y += line_height
        cursor_y += 12

    draw_block(clean_title, "title")

    toc = extract_table_of_contents(markdown_content)
    if toc:
        draw_block("Оглавление", "h2")
        for entry in toc:
            indent = max(0, min(3, entry.level - 1)) * 28
            draw_block(f"• {entry.title}", "small", indent=indent)
        cursor_y += 18

    for raw_line in markdown_content.splitlines():
        line = html.unescape(sanitize_markdown_text(raw_line.strip()))
        if not line:
            cursor_y += 10
            continue
        if line.startswith("#"):
            prefix, separator, heading = line.partition(" ")
            if separator and set(prefix) == {"#"}:
                level = len(prefix)
                draw_block(heading.strip(), "h1" if level == 1 else "h2" if level == 2 else "h3")
                continue
        if line.startswith(("- ", "* ")):
            draw_block(f"• {line[2:].strip()}", "body", indent=24)
        else:
            draw_block(line, "body")

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
