from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from konspekt.diagnostics import _installed_tesseract_languages
from scripts.smoke_package import (
    _is_linux_graphical_session,
    _is_macos_graphical_session,
    is_ci_environment,
    is_falsy,
    is_truthy,
    main,
    should_run_gui_smoke,
    validate_diagnostics,
)


def _valid_diagnostics() -> dict[str, object]:
    return {
        "status": "ok",
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "python_version": "3.12.0",
        },
        "directories_writable": {
            "data": True,
            "cache": True,
            "logs": True,
            "temp": True,
        },
        "dependencies": {
            "ffmpeg_available": True,
            "tesseract_available": True,
            "tesseract_languages": ["eng", "rus"],
        },
        "manifest_schema_version": 1,
        "webview_gui": "cocoa",
    }


class SmokePackageTests(unittest.TestCase):
    def test_is_truthy(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "YES", "on", "t", "y", "  1  "):
            self.assertTrue(is_truthy(value), f"Expected {value!r} to be truthy")
        for value in ("0", "false", "no", "off", "", None, "random"):
            self.assertFalse(is_truthy(value), f"Expected {value!r} to be falsy")

    def test_is_falsy(self) -> None:
        for value in ("0", "false", "FALSE", "no", "NO", "off", "f", "n", "  0  "):
            self.assertTrue(is_falsy(value), f"Expected {value!r} to be falsy")
        for value in ("1", "true", "yes", "on", "", None, "random"):
            self.assertFalse(is_falsy(value), f"Expected {value!r} not to match is_falsy")

    def test_is_ci_environment(self) -> None:
        self.assertTrue(is_ci_environment({"CI": "true"}))
        self.assertTrue(is_ci_environment({"GITHUB_ACTIONS": "1"}))
        self.assertTrue(is_ci_environment({"CONTINUOUS_INTEGRATION": "yes"}))
        self.assertTrue(is_ci_environment({"TF_BUILD": "True"}))
        self.assertFalse(is_ci_environment({}))
        self.assertFalse(is_ci_environment({"CI": "0", "GITHUB_ACTIONS": "false"}))

    def test_should_run_gui_smoke_explicit_opt_in_overrides_ci_and_platform(self) -> None:
        environment = {"KONSPEKT_GUI_SMOKE": "1", "CI": "true"}
        run_gui, reason = should_run_gui_smoke(env=environment, platform="darwin")
        self.assertTrue(run_gui)
        self.assertIn("KONSPEKT_GUI_SMOKE", reason)

        yes_environment = {"KONSPEKT_GUI_SMOKE": "yes", "CI": "true"}
        run_gui, _ = should_run_gui_smoke(env=yes_environment, platform="win32")
        self.assertTrue(run_gui)

    def test_should_run_gui_smoke_explicit_opt_out(self) -> None:
        run_gui, reason = should_run_gui_smoke(
            env={"KONSPEKT_GUI_SMOKE": "0"},
            platform="darwin",
        )
        self.assertFalse(run_gui)
        self.assertIn("disabled", reason)

    def test_should_run_gui_smoke_ci_defaults_to_skip(self) -> None:
        run_gui, reason = should_run_gui_smoke(env={"CI": "true"}, platform="darwin")
        self.assertFalse(run_gui)
        self.assertIn("CI", reason)

    def test_should_run_gui_smoke_darwin_ssh_skips(self) -> None:
        environment = {"SSH_CONNECTION": "10.0.0.1 52222 10.0.0.2 22"}
        run_gui, reason = should_run_gui_smoke(env=environment, platform="darwin")
        self.assertFalse(run_gui)
        self.assertIn("SSH", reason)

        run_gui, _ = _is_macos_graphical_session({"SSH_CLIENT": "10.0.0.1 52222 22"})
        self.assertFalse(run_gui)

    def test_should_run_gui_smoke_linux_display_check(self) -> None:
        self.assertFalse(_is_linux_graphical_session({})[0])
        self.assertTrue(_is_linux_graphical_session({"DISPLAY": ":0"})[0])
        self.assertTrue(_is_linux_graphical_session({"WAYLAND_DISPLAY": "wayland-0"})[0])
        self.assertFalse(should_run_gui_smoke(env={}, platform="linux")[0])
        self.assertTrue(should_run_gui_smoke(env={"DISPLAY": ":0"}, platform="linux")[0])

    def test_tesseract_language_discovery_reads_nonempty_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "tesseract"
            executable.write_bytes(b"binary")
            tessdata = root / "tessdata"
            tessdata.mkdir()
            (tessdata / "eng.traineddata").write_bytes(b"eng")
            (tessdata / "rus.traineddata").write_bytes(b"rus")
            (tessdata / "empty.traineddata").touch()

            self.assertEqual(
                _installed_tesseract_languages(str(executable)),
                ["eng", "rus"],
            )

    def test_validate_diagnostics(self) -> None:
        valid_payload = _valid_diagnostics()
        valid, reason = validate_diagnostics(valid_payload)
        self.assertTrue(valid)
        self.assertEqual(reason, "")

        self.assertFalse(validate_diagnostics(dict(valid_payload, status="error"))[0])

        missing_dependency = dict(
            valid_payload,
            dependencies={
                "ffmpeg_available": True,
                "tesseract_available": False,
                "tesseract_languages": ["eng", "rus"],
            },
        )
        self.assertFalse(validate_diagnostics(missing_dependency)[0])

        missing_russian = dict(
            valid_payload,
            dependencies={
                "ffmpeg_available": True,
                "tesseract_available": True,
                "tesseract_languages": ["eng"],
            },
        )
        valid, reason = validate_diagnostics(missing_russian)
        self.assertFalse(valid)
        self.assertIn("rus", reason)

        unwritable = dict(
            valid_payload,
            directories_writable={"data": True, "cache": False},
        )
        self.assertFalse(validate_diagnostics(unwritable)[0])
        self.assertFalse(validate_diagnostics("not-a-dict")[0])

    @patch("scripts.smoke_package.subprocess.run")
    @patch("scripts.smoke_package.find_artifact_executable")
    def test_main_gui_failure_returns_nonzero_code(
        self,
        mock_find: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/tmp/fake-Konspekt")
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(_valid_diagnostics()),
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=134,
                stdout="",
                stderr="TclError: NaN",
            ),
        ]

        with patch.dict("os.environ", {"KONSPEKT_GUI_SMOKE": "1"}, clear=False):
            code = main(["--artifact", "/tmp/fake-Konspekt"])
        self.assertEqual(code, 134)

    @patch("scripts.smoke_package.subprocess.run")
    @patch("scripts.smoke_package.find_artifact_executable")
    def test_main_gui_timeout_returns_nonzero_code(
        self,
        mock_find: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/tmp/fake-Konspekt")
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(_valid_diagnostics()),
                stderr="",
            ),
            subprocess.TimeoutExpired(cmd="--smoke-test-gui", timeout=15),
        ]

        with patch.dict("os.environ", {"KONSPEKT_GUI_SMOKE": "1"}, clear=False):
            code = main(["--artifact", "/tmp/fake-Konspekt"])
        self.assertEqual(code, 1)

    @patch("scripts.smoke_package.subprocess.run")
    @patch("scripts.smoke_package.find_artifact_executable")
    def test_main_gui_skip_returns_zero(
        self,
        mock_find: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_find.return_value = Path("/tmp/fake-Konspekt")
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(_valid_diagnostics()),
                stderr="",
            )
        ]

        with patch.dict(
            "os.environ",
            {"CI": "true", "KONSPEKT_GUI_SMOKE": ""},
            clear=False,
        ):
            code = main(["--artifact", "/tmp/fake-Konspekt"])
        self.assertEqual(code, 0)
        self.assertEqual(mock_run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
