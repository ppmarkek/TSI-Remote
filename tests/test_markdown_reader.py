from __future__ import annotations

import unittest

from konspekt.markdown_reader import (
    extract_table_of_contents,
    extract_timestamps,
    sanitize_markdown_text,
)


class MarkdownReaderTests(unittest.TestCase):
    def test_sanitize_markdown_text(self) -> None:
        malicious = (
            '<script>alert("xss")</script><p>Safe text</p><a href="javascript:doEvil()">link</a>'
        )
        clean = sanitize_markdown_text(malicious)

        self.assertNotIn("<script>", clean)
        self.assertNotIn("javascript:", clean)
        self.assertIn("Safe text", clean)

    def test_extract_table_of_contents(self) -> None:
        markdown = """# Main Lecture Title
Some intro text...

## 1. Overview of Algorithms
Detail 1

### 1.1 Complexity
Detail 2

## 2. Practical Examples
Detail 3
"""
        toc = extract_table_of_contents(markdown)
        self.assertEqual(len(toc), 4)
        self.assertEqual(toc[0].level, 1)
        self.assertEqual(toc[0].title, "Main Lecture Title")
        self.assertEqual(toc[1].level, 2)
        self.assertEqual(toc[1].title, "1. Overview of Algorithms")
        self.assertEqual(toc[2].level, 3)
        self.assertEqual(toc[2].title, "1.1 Complexity")

    def test_extract_timestamps(self) -> None:
        markdown = """Lecture starts at 01:30 with introductory slides.
Later at 14:45 we explore quicksort.
Extended discussion at 01:25:30 about tree traversals.
"""
        ts = extract_timestamps(markdown)
        self.assertEqual(len(ts), 3)

        # 01:30 -> 90 seconds
        self.assertEqual(ts[0].raw_str, "01:30")
        self.assertEqual(ts[0].total_seconds, 90.0)

        # 14:45 -> 885 seconds
        self.assertEqual(ts[1].raw_str, "14:45")
        self.assertEqual(ts[1].total_seconds, 885.0)

        # 01:25:30 -> 1*3600 + 25*60 + 30 = 5130 seconds
        self.assertEqual(ts[2].raw_str, "01:25:30")
        self.assertEqual(ts[2].total_seconds, 5130.0)


if __name__ == "__main__":
    unittest.main()
