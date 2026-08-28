"""Clean HTML and printable document export for generated lesson notes."""

from __future__ import annotations

import html
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
