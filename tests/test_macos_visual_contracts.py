from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from konspekt.app import PALETTE, StudyApp, Typography, _scrollbar_should_be_visible

ROOT = Path(__file__).resolve().parents[1]


class _RecordingStyle:
    def __init__(self) -> None:
        self.selected_theme: str | None = None
        self.configurations: dict[str, dict[str, object]] = {}
        self.mappings: dict[str, dict[str, object]] = {}
        self.layouts: dict[str, object] = {}

    def theme_names(self) -> tuple[str, ...]:
        return ("aqua", "clam", "alt", "default")

    def theme_use(self, name: str) -> None:
        self.selected_theme = name

    def configure(self, style_name: str, **options: object) -> None:
        self.configurations[style_name] = options

    def map(self, style_name: str, **options: object) -> None:
        self.mappings[style_name] = options

    def layout(self, style_name: str, layout: object) -> None:
        self.layouts[style_name] = layout


class MacOSVisualContractsTests(unittest.TestCase):
    def test_custom_theme_fully_styles_macos_comboboxes_and_scrollbars(self) -> None:
        style = _RecordingStyle()
        app = type(
            "StyleHarness",
            (),
            {
                "style": style,
                "type": Typography(
                    family="Helvetica Neue",
                    title=("Helvetica Neue", 24, "bold"),
                    heading=("Helvetica Neue", 15, "bold"),
                    subheading=("Helvetica Neue", 11, "bold"),
                    body=("Helvetica Neue", 11),
                    body_bold=("Helvetica Neue", 11, "bold"),
                    secondary=("Helvetica Neue", 10),
                    small=("Helvetica Neue", 10),
                ),
            },
        )()

        with patch.object(sys, "platform", "darwin"):
            StudyApp._configure_styles(app)  # type: ignore[arg-type]

        self.assertEqual(style.selected_theme, "clam")
        combo = style.configurations["Settings.TCombobox"]
        self.assertEqual(combo["background"], PALETTE["surface"])
        self.assertEqual(combo["arrowcolor"], PALETTE["faint"])
        self.assertEqual(combo["bordercolor"], PALETTE["line"])
        readonly_fields = style.mappings["Settings.TCombobox"]["fieldbackground"]
        self.assertIn(("readonly", PALETTE["surface"]), readonly_fields)
        self.assertIn("Vertical.TScrollbar", style.layouts)
        scrollbar = style.configurations["Vertical.TScrollbar"]
        self.assertEqual(scrollbar["troughcolor"], PALETTE["canvas"])
        self.assertEqual(scrollbar["background"], PALETTE["line"])

    def test_macos_icon_keeps_transparent_safe_area(self) -> None:
        for path in (
            ROOT / "assets" / "konspekt.icns",
            ROOT / "assets" / "konspekt-macos.png",
        ):
            with self.subTest(path=path.name), Image.open(path) as source:
                icon = source.convert("RGBA")

                alpha_bounds = icon.getchannel("A").getbbox()
                self.assertIsNotNone(alpha_bounds)
                assert alpha_bounds is not None
                left, top, right, bottom = alpha_bounds
                self.assertLessEqual(right - left, round(icon.width * 0.82))
                self.assertLessEqual(bottom - top, round(icon.height * 0.82))
                for corner in ((0, 0), (icon.width - 1, 0), (0, icon.height - 1)):
                    self.assertEqual(icon.getpixel(corner)[3], 0)

    def test_scrollbar_is_hidden_when_the_whole_canvas_is_visible(self) -> None:
        self.assertFalse(_scrollbar_should_be_visible("0.0", "1.0"))
        self.assertTrue(_scrollbar_should_be_visible("0.0", "0.72"))
        self.assertTrue(_scrollbar_should_be_visible("0.12", "1.0"))

    def test_visual_palette_still_has_accessible_focus_color(self) -> None:
        self.assertEqual(PALETTE["focus"], "#B45309")


if __name__ == "__main__":
    unittest.main()
