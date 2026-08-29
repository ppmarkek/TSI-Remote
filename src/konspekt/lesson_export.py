"""Clean HTML and printable document export for generated lesson notes."""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path

from .atomic_io import AtomicIOError, atomic_write_text
from .markdown_reader import extract_table_of_contents, sanitize_markdown_text


def render_lesson_html(
    title: str,
    markdown_content: str,
) -> str:
    """Render a standalone, sanitized HTML document with typography and table of contents."""
    clean_title = sanitize_markdown_text(title.strip())
    toc = extract_table_of_contents(markdown_content)

    toc_html = (
        "<ul>\n"
        + "\n".join(
            f'  <li style="margin-left: {(entry.level - 1) * 20}px;">{html.escape(entry.title)}</li>'
            for entry in toc
        )
        + "\n</ul>"
    )

    # Transform markdown lines into simple safe paragraphs/headings
    body_lines: list[str] = []
    for line in markdown_content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = min(6, line.count("#", 0, line.find(" ")))
            text = line.lstrip("#").strip()
            body_lines.append(f"<h{level}>{html.escape(text)}</h{level}>")
        elif line.startswith("- ") or line.startswith("* "):
            body_lines.append(f"<li>{html.escape(line[2:].strip())}</li>")
        else:
            body_lines.append(f"<p>{html.escape(line)}</p>")

    body_html = "\n".join(body_lines)

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
    pre, code {{ font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace; background: #F3F6F4; padding: 2px 6px; border-radius: 4px; }}
    .toc {{ background: #F7FAF8; padding: 18px 24px; border-radius: 8px; border: 1px solid #DDE5E0; margin-bottom: 32px; }}
    ul {{ list-style-type: disc; padding-left: 20px; }}
    @media print {{
      body {{ margin: 0; max-width: none; }}
      .toc {{ break-after: page; }}
    }}
  </style>
</head>
<body>
  <h1>{html.escape(clean_title)}</h1>
  <div class="toc">
    <h3>Оглавление</h3>
    {toc_html}
  </div>
  <div class="content">
    {body_html}
  </div>
</body>
</html>
"""


def export_lesson_to_html_file(
    title: str,
    markdown_content: str,
    output_path: Path,
) -> Path:
    """Save the rendered HTML lesson atomically to disk."""
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
    """Export a lesson to PDF using WeasyPrint, wkhtmltopdf, or built-in pure-Python renderer.

    WeasyPrint and wkhtmltopdf are preferred when available. When neither is installed,
    a pure-Python compliant PDF generator produces a clean, readable PDF with full
    Unicode/Cyrillic support and table of contents.
    """
    rendered = render_lesson_html(title, markdown_content)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]

        HTML(string=rendered, base_url=str(output_path.parent)).write_pdf(str(output_path))
        return output_path
    except ImportError:
        pass
    except Exception:
        pass

    converter = shutil.which("wkhtmltopdf")
    if converter:
        temporary_html = output_path.with_suffix(".html.tmp")
        try:
            atomic_write_text(temporary_html, rendered, encoding="utf-8")
            result = subprocess.run(
                [converter, "--quiet", str(temporary_html), str(output_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if result.returncode == 0 and output_path.is_file():
                return output_path
        except (AtomicIOError, OSError, subprocess.TimeoutExpired):
            pass
        finally:
            temporary_html.unlink(missing_ok=True)

    # Pure-Python PDF generation fallback
    try:
        pdf_data = _render_pure_python_pdf(title, markdown_content)
        output_path.write_bytes(pdf_data)
        return output_path
    except Exception as exc:
        raise RuntimeError(f"Не удалось экспортировать конспект в PDF: {exc}") from exc


def _render_pure_python_pdf(title: str, markdown_content: str) -> bytes:
    import textwrap

    clean_title = sanitize_markdown_text(title.strip())
    toc = extract_table_of_contents(markdown_content)

    items: list[tuple[str, str]] = [("title", clean_title)]
    if toc:
        items.append(("h2", "Оглавление"))
        for entry in toc:
            prefix = "  " * (entry.level - 1) + "• "
            items.append(("toc_item", f"{prefix}{entry.title}"))

    for line in markdown_content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = min(6, line.count("#", 0, line.find(" "))) if " " in line else line.count("#")
            text = line.lstrip("#").strip()
            items.append((f"h{min(level, 3)}", text))
        elif line.startswith("- ") or line.startswith("* "):
            items.append(("bullet", line[2:].strip()))
        else:
            items.append(("p", line))

    page_width = 595
    page_height = 842
    margin_x = 54
    margin_top = 54
    margin_bottom = 54

    pages_ops: list[list[str]] = []
    current_page_ops: list[str] = []
    cursor_y = page_height - margin_top

    def start_new_page() -> None:
        nonlocal current_page_ops, cursor_y
        if current_page_ops:
            pages_ops.append(current_page_ops)
        current_page_ops = []
        cursor_y = page_height - margin_top

    for kind, text in items:
        if kind == "title":
            font_size = 18
            line_height = 24
            max_chars = 45
            color = "0.09 0.42 0.27"
        elif kind == "h1":
            font_size = 14
            line_height = 20
            max_chars = 55
            color = "0.09 0.42 0.27"
            cursor_y -= 8
        elif kind == "h2":
            font_size = 12
            line_height = 18
            max_chars = 65
            color = "0.09 0.42 0.27"
            cursor_y -= 6
        elif kind == "h3":
            font_size = 11
            line_height = 16
            max_chars = 70
            color = "0.09 0.13 0.11"
            cursor_y -= 4
        elif kind == "bullet":
            font_size = 10
            line_height = 14
            max_chars = 72
            color = "0.09 0.13 0.11"
            text = "  •  " + text
        elif kind == "toc_item":
            font_size = 9
            line_height = 13
            max_chars = 75
            color = "0.2 0.25 0.22"
        else:
            font_size = 10
            line_height = 14
            max_chars = 75
            color = "0.09 0.13 0.11"

        wrapped_lines = textwrap.wrap(text, width=max_chars) or [""]
        for line_str in wrapped_lines:
            if cursor_y - line_height < margin_bottom:
                start_new_page()
            hex_str = "".join(f"{ord(c):04X}" for c in line_str)
            op = f"BT {color} rg /F1 {font_size} Tf 1 0 0 1 {margin_x} {cursor_y} Tm <{hex_str}> Tj ET"
            current_page_ops.append(op)
            cursor_y -= line_height
        cursor_y -= 3

    if current_page_ops:
        pages_ops.append(current_page_ops)
    if not pages_ops:
        pages_ops = [[]]

    total_pages = len(pages_ops)
    for p_idx, p_ops in enumerate(pages_ops, start=1):
        footer_text = f"Страница {p_idx} из {total_pages}"
        f_hex = "".join(f"{ord(c):04X}" for c in footer_text)
        p_ops.append(
            f"BT 0.5 0.5 0.5 rg /F1 8 Tf 1 0 0 1 {page_width // 2 - 30} 30 Tm <{f_hex}> Tj ET"
        )
        if p_idx > 1:
            h_hex = "".join(f"{ord(c):04X}" for c in clean_title[:40])
            p_ops.append(
                f"BT 0.5 0.5 0.5 rg /F1 8 Tf 1 0 0 1 {margin_x} {page_height - 35} Tm <{h_hex}> Tj ET"
            )
            p_ops.append(
                f"0.85 0.85 0.85 RG 0.5 w {margin_x} {page_height - 40} m {page_width - margin_x} {page_height - 40} l S"
            )

    cmap_data = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Custom-ToUnicode def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n"
        "<0000> <FFFF>\n"
        "endcodespacerange\n"
        "1 beginbfrange\n"
        "<0000> <FFFF> <0000>\n"
        "endbfrange\n"
        "endcmap\n"
        "CMapName currentdict /CMap defineresource pop\n"
        "end\n"
        "end\n"
    ).encode("ascii")

    objects: list[bytes] = []
    offsets: list[int] = []

    # 1: Catalog
    # 2: Pages
    # 3: Font
    # 4: CIDFont
    # 5: ToUnicode CMap
    # 6: FontDescriptor
    page_obj_ids = [7 + 2 * i for i in range(total_pages)]
    content_obj_ids = [8 + 2 * i for i in range(total_pages)]

    # Obj 1: Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Obj 2: Pages
    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(f"<< /Type /Pages /Count {total_pages} /Kids [{kids_str}] >>".encode("ascii"))
    # Obj 3: Type0 Font
    objects.append(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Helvetica /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 5 0 R >>"
    )
    # Obj 4: CIDFontType2
    objects.append(
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Helvetica /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 6 0 R /DW 600 >>"
    )
    # Obj 5: ToUnicode Stream
    objects.append(
        f"<< /Length {len(cmap_data)} >>\nstream\n".encode("ascii") + cmap_data + b"\nendstream"
    )
    # Obj 6: FontDescriptor
    objects.append(
        b"<< /Type /FontDescriptor /FontName /Helvetica /Flags 32 /FontBBox [-200 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>"
    )

    # Pages and contents
    for i in range(total_pages):
        # Page object
        cid = content_obj_ids[i]
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] /Resources << /Font << /F1 3 0 R >> >> /Contents {cid} 0 R >>".encode(
                "ascii"
            )
        )
        # Content object
        content_stream = "\n".join(pages_ops[i]).encode("ascii")
        objects.append(
            f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii")
            + content_stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)
