from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from konspekt.lesson_export import (
    _render_pdf_page_images,
    export_lesson_to_html_file,
    export_lesson_to_pdf_file,
    render_lesson_html,
)


class LessonExportTests(unittest.TestCase):
    def test_render_lesson_html_contains_toc_and_cyrillic(self) -> None:
        title = "Лекция по теории алгоритмов"
        markdown = """# Введение
Основные понятия.

## Временная сложность
Асимптотический анализ O(N).
"""
        html_out = render_lesson_html(title, markdown)

        self.assertIn("Лекция по теории алгоритмов", html_out)
        self.assertIn("Временная сложность", html_out)
        self.assertIn("Оглавление", html_out)
        self.assertIn("<!DOCTYPE html>", html_out)

    def test_render_lesson_html_uses_the_reader_markup(self) -> None:
        html_out = render_lesson_html(
            "Конспект <b>",
            "# Тема\n\n## Сортировки [00:12:34]\n\n"
            "| Алгоритм | Сложность |\n|---|---|\n| **Быстрая** | O(n log n) |\n\n"
            "<script>alert(1)</script>",
        )

        self.assertIn('<a href="#sec-2">Сортировки</a>', html_out)
        self.assertIn('<h2 id="sec-2">', html_out)
        self.assertIn('<span class="ts">12:34</span>', html_out)
        self.assertIn("<td><strong>Быстрая</strong></td>", html_out)
        self.assertIn("<title>Конспект &lt;b&gt;</title>", html_out)
        self.assertNotIn("<script", html_out)

    def test_render_lesson_html_without_headings(self) -> None:
        html_out = render_lesson_html("Пусто", "")

        self.assertIn("Разделы не найдены.", html_out)
        self.assertNotIn("doc-meta", html_out.split("</style>")[1])

    def test_export_lesson_to_html_file_writes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_file = Path(temp_dir) / "lesson.html"
            export_lesson_to_html_file(
                "Тестовый конспект",
                "# Заголовок\nТекст конспекта.",
                out_file,
            )

            self.assertTrue(out_file.is_file())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("Тестовый конспект", content)

    def test_export_lesson_to_pdf_file_generates_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_file = Path(temp_dir) / "lesson.pdf"
            export_lesson_to_pdf_file(
                "Лекция по базам данных",
                "# Введение\nРеляционная модель данных.\n## SQL\n- Запросы SELECT\n- Индексы B-Tree\n",
                out_file,
            )

            self.assertTrue(out_file.is_file())
            pdf_bytes = out_file.read_bytes()
            self.assertTrue(pdf_bytes.startswith(b"%PDF-1.4"))
            self.assertTrue(pdf_bytes.rstrip().endswith(b"%%EOF"))
            self.assertGreater(len(pdf_bytes), 200)

    def test_builtin_pdf_renderer_handles_rich_markdown(self) -> None:
        pages = _render_pdf_page_images(
            "Конспект",
            "# Тема\n\n1. **Вопрос?**\n   Ответ: да.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nx = 1\n```\n\n"
            "> [!TIP]\n> Совет\n\n- [x] задача\n",
        )

        self.assertGreaterEqual(len(pages), 1)


if __name__ == "__main__":
    unittest.main()
