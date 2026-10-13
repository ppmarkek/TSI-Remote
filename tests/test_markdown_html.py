from __future__ import annotations

import unittest

from konspekt.markdown_html import (
    find_timestamps,
    format_clock,
    parse_timestamp,
    render_inline,
    render_markdown,
    split_table_row,
    strip_inline_markdown,
)


class RenderMarkdownTests(unittest.TestCase):
    def test_headings_get_anchors_that_match_the_outline(self) -> None:
        result = render_markdown("# Тема [00:00:05]\n\n## Раздел **один**\n\nТекст")

        self.assertEqual(
            [(item.level, item.title, item.anchor) for item in result.headings],
            [(1, "Тема", "sec-1"), (2, "Раздел один", "sec-2")],
        )
        self.assertIn('<h1 id="sec-1">', result.html)
        self.assertIn('<h2 id="sec-2">Раздел <strong>один</strong></h2>', result.html)
        self.assertEqual(result.word_count, 4)

    def test_raw_html_and_dangerous_links_are_neutralised(self) -> None:
        html = render_markdown(
            '<script>alert(1)</script> <img src=x onerror="y">\n'
            "[плохо](javascript:alert(1)) [хорошо](https://example.com/a?b=1&c=2)"
        ).html

        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("плохо", html)
        self.assertIn(
            '<a class="ext" href="https://example.com/a?b=1&amp;c=2" target="_blank" '
            'rel="noopener noreferrer">хорошо</a>',
            html,
        )

    def test_control_characters_cannot_forge_placeholders(self) -> None:
        html = render_markdown("a \x020\x03 b &#2;0&#3; `code`").html

        self.assertEqual(html, "<p>a 0 b 0 <code>code</code></p>")

    def test_timestamps_become_buttons(self) -> None:
        html = render_inline("См. [00:12:34], [01:02–01:30] и 1:02:03, но не в 10:30")

        self.assertIn('data-t="754"', html)
        self.assertIn('data-t="62"', html)
        self.assertIn('data-t="90"', html)
        self.assertIn('data-t="3723"', html)
        self.assertNotIn('data-t="630"', html)
        self.assertIn(">12:34</button>", html)

        exported = render_inline("[00:12:34]", timestamp_tag="span")
        self.assertEqual(exported, '<span class="ts">12:34</span>')

    def test_nested_lists_tasks_and_lazy_answers(self) -> None:
        html = render_markdown(
            "- один\n  - вложенный\n- [ ] задача\n- [x] готово\n\n"
            "1. Вопрос?\nОтвет: да.\n2. Второй\n"
        ).html

        self.assertIn("<li>один\n<ul>\n<li>вложенный</li>\n</ul></li>", html)
        self.assertIn('<li class="task"><span class="check">☐</span> задача</li>', html)
        self.assertIn('<span class="check">☑</span> готово', html)
        self.assertIn("<ol>\n<li>Вопрос?<br>\nОтвет: да.</li>\n<li>Второй</li>\n</ol>", html)

    def test_ordered_list_keeps_its_start_number(self) -> None:
        html = render_markdown("Текст\n\n3. третий\n4. четвёртый").html

        self.assertIn('<ol start="3">', html)

    def test_tables_with_alignment_and_escaped_pipes(self) -> None:
        html = render_markdown(
            "| Алгоритм | Сложность |\n|:--|--:|\n| Быстрая \\| QS | `a|b` |\n| Пузырёк |"
        ).html

        self.assertIn('<div class="table-wrap"><table>', html)
        self.assertIn('<th class="al-left">Алгоритм</th>', html)
        self.assertIn('<td class="al-right"><code>a|b</code></td>', html)
        self.assertIn("Быстрая | QS", html)
        self.assertIn('<td class="al-right"></td>', html)

    def test_code_blocks_quotes_and_rules(self) -> None:
        html = render_markdown(
            "```python\nx = '<b>' * 2\n```\n\n> [!WARNING]\n> Осторожно\n\n---\n\n![схема](a.png)"
        ).html

        self.assertIn("<pre><code class=\"lang-python\">x = '&lt;b&gt;' * 2</code></pre>", html)
        self.assertIn('<blockquote class="callout callout-warning">', html)
        self.assertIn("<strong>Внимание.</strong>", html)
        self.assertIn("<hr>", html)
        self.assertIn('<span class="img-alt">[изображение: схема]</span>', html)

    def test_emphasis_does_not_break_words_or_nest_wrongly(self) -> None:
        self.assertEqual(render_inline("snake_case_name"), "snake_case_name")
        self.assertEqual(render_inline("2*3*4"), "2*3*4")
        self.assertEqual(render_inline("***оба***"), "<strong><em>оба</em></strong>")
        html = render_inline("**a *b** c*")
        self.assertEqual(html.count("<strong>"), html.count("</strong>"))
        self.assertEqual(html.count("<em>"), html.count("</em>"))

    def test_bare_urls_drop_trailing_punctuation(self) -> None:
        html = render_inline("Смотри https://example.com/a_(b). И https://x.org!")

        self.assertIn('href="https://example.com/a_(b)"', html)
        self.assertIn('href="https://x.org"', html)
        self.assertTrue(html.endswith("</a>!"))


class HelperTests(unittest.TestCase):
    def test_strip_inline_markdown(self) -> None:
        self.assertEqual(
            strip_inline_markdown("**Термин** — `код` и [ссылка](http://x) \\*"),
            "Термин — код и ссылка *",
        )

    def test_parse_and_format_timestamps(self) -> None:
        self.assertEqual(parse_timestamp("[01:02:03]"), 3723)
        self.assertEqual(parse_timestamp("12:34"), 754)
        self.assertIsNone(parse_timestamp("12:99"))
        self.assertIsNone(parse_timestamp("abc"))
        self.assertEqual(format_clock(754), "12:34")
        self.assertEqual(format_clock(3723), "1:02:03")

    def test_find_timestamps_in_order_without_repeats(self) -> None:
        self.assertEqual(
            find_timestamps("[00:12:34] потом 00:01:00 и [00:12:34–00:13:00], в 10:30"),
            [754, 60, 780],
        )

    def test_split_table_row(self) -> None:
        self.assertEqual(split_table_row("| a | `b|c` | d \\| e |"), ["a", "`b|c`", "d \\| e"])


if __name__ == "__main__":
    unittest.main()
