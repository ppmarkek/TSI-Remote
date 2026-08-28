from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.platform_services import (
    PlatformAppearancePreferences,
    PlatformAppPaths,
    PlatformDependencyLocator,
    PlatformKeyboardConventions,
    file_manager_command,
    webview_gui_for_platform,
)


class PlatformServicesTests(unittest.TestCase):
    def test_test_root_confines_all_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = PlatformAppPaths(test_root=root)
            paths.ensure_directories()

            self.assertEqual(paths.data_dir, root / "data")
            self.assertEqual(paths.cache_dir, root / "cache")
            self.assertEqual(paths.log_dir, root / "logs")
            self.assertEqual(paths.temp_dir, root / "temp")
            self.assertEqual(paths.library_path, root / "data" / "library.json")
            self.assertEqual(paths.settings_path, root / "data" / "settings.json")
            self.assertEqual(paths.lectures_dir, root / "data" / "lectures")
            self.assertEqual(paths.diagnostic_dir, root / "logs" / "diagnostics")

            self.assertTrue((root / "data").is_dir())
            self.assertTrue((root / "cache").is_dir())
            self.assertTrue((root / "logs").is_dir())

    def test_macos_standard_paths(self) -> None:
        paths = PlatformAppPaths(system_override="darwin")
        # On macOS, user_data_dir is under Library/Application Support
        self.assertIn("Application Support", str(paths.data_dir))
        self.assertIn("Caches", str(paths.cache_dir))
        self.assertIn("Logs", str(paths.log_dir))

    def test_windows_standard_paths(self) -> None:
        with patch.dict("os.environ", {"LOCALAPPDATA": "C:/Users/Student/AppData/Local"}):
            paths = PlatformAppPaths(system_override="win32")
            self.assertEqual(paths.data_dir, Path("C:/Users/Student/AppData/Local/Konspekt"))

    def test_keyboard_conventions_by_platform(self) -> None:
        mac_kb = PlatformKeyboardConventions(system_override="darwin")
        win_kb = PlatformKeyboardConventions(system_override="win32")

        self.assertEqual(mac_kb.primary_modifier, "⌘")
        self.assertEqual(mac_kb.format_shortcut("V"), "⌘V")
        self.assertEqual(mac_kb.format_shortcut("C"), "⌘C")

        self.assertEqual(win_kb.primary_modifier, "Ctrl")
        self.assertEqual(win_kb.format_shortcut("V"), "Ctrl+V")
        self.assertEqual(win_kb.format_shortcut("C"), "Ctrl+C")

    def test_appearance_preferences_by_platform(self) -> None:
        mac_ui = PlatformAppearancePreferences(system_override="darwin")
        win_ui = PlatformAppearancePreferences(system_override="win32")

        self.assertEqual(mac_ui.font_family_body, "Helvetica Neue")
        self.assertEqual(mac_ui.font_family_code, "Menlo")

        self.assertEqual(win_ui.font_family_body, "Segoe UI")
        self.assertEqual(win_ui.font_family_code, "Consolas")

    def test_file_manager_command_construction(self) -> None:
        target = Path("/tmp/lecture")
        self.assertEqual(file_manager_command(target, "darwin"), ["open", str(target.resolve())])
        self.assertEqual(
            file_manager_command(target, "win32"), ["explorer.exe", str(target.resolve())]
        )
        self.assertEqual(file_manager_command(target, "linux"), ["xdg-open", str(target.resolve())])

    def test_webview_gui_selection(self) -> None:
        self.assertEqual(webview_gui_for_platform("darwin"), "cocoa")
        self.assertEqual(webview_gui_for_platform("win32"), "edgechromium")
        self.assertEqual(webview_gui_for_platform("linux"), "gtk")

    def test_dependency_locator_discovers_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir)
            custom_bin = bin_dir / "custom_tool"
            custom_bin.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            custom_bin.chmod(0o755)

            locator = PlatformDependencyLocator(extra_paths=(bin_dir,))
            found = locator.find_executable("custom_tool")
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).resolve(), custom_bin.resolve())


if __name__ == "__main__":
    unittest.main()
