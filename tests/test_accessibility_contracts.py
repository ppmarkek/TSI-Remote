from __future__ import annotations

import unittest

from konspekt.app import PALETTE
from konspekt.platform_services import (
    PlatformAppearancePreferences,
    PlatformKeyboardConventions,
)


class AccessibilityContractsTests(unittest.TestCase):
    def test_keyboard_shortcut_conventions(self) -> None:
        mac_conv = PlatformKeyboardConventions(target_os="darwin")
        win_conv = PlatformKeyboardConventions(target_os="win32")

        self.assertEqual(mac_conv.format_shortcut("V"), "⌘V")
        self.assertEqual(win_conv.format_shortcut("V"), "Ctrl+V")
        self.assertEqual(mac_conv.primary_modifier, "⌘")
        self.assertEqual(win_conv.primary_modifier, "Ctrl")

    def test_platform_typography_defaults(self) -> None:
        mac_pref = PlatformAppearancePreferences(target_os="darwin")
        win_pref = PlatformAppearancePreferences(target_os="win32")

        self.assertEqual(mac_pref.font_family_body, "Helvetica Neue")
        self.assertEqual(win_pref.font_family_body, "Segoe UI")
        self.assertEqual(mac_pref.font_family_code, "Menlo")
        self.assertEqual(win_pref.font_family_code, "Consolas")

    def test_palette_color_integrity(self) -> None:
        # Verify critical high-contrast palette colors are defined
        self.assertIn("canvas", PALETTE)
        self.assertIn("ink", PALETTE)
        self.assertIn("primary", PALETTE)
        self.assertIn("focus", PALETTE)
        self.assertEqual(PALETTE["canvas"], "#FFFFFF")
        self.assertTrue(PALETTE["ink"].startswith("#"))


if __name__ == "__main__":
    unittest.main()
