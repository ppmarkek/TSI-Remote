#!/usr/bin/env python3
"""Smoke test a built artifact with isolated diagnostics and optional GUI startup."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_TESSERACT_LANGUAGES = {"eng", "rus"}


def find_artifact_executable(artifact: Path | None = None) -> Path | None:
    if artifact is not None:
        candidate = artifact.expanduser().resolve()
        if candidate.is_file():
            return candidate
        candidates = (
            candidate / "Contents" / "MacOS" / "Konspekt",
            candidate / "Konspekt.exe",
            candidate / "Konspekt",
        )
        return next((item for item in candidates if item.is_file()), None)
    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        binary = dist_dir / "Konspekt.app" / "Contents" / "MacOS" / "Konspekt"
        if binary.is_file():
            return binary
        alternative = dist_dir / "Konspekt" / "Konspekt"
        if alternative.is_file():
            return alternative
    elif sys.platform == "win32":
        executable = dist_dir / "Konspekt" / "Konspekt.exe"
        if executable.is_file():
            return executable
    else:
        binary = dist_dir / "Konspekt" / "Konspekt"
        if binary.is_file():
            return binary
    return None


def validate_diagnostics(payload: object) -> tuple[bool, str]:
    """Validate the complete packaged-runtime contract."""

    if not isinstance(payload, dict):
        return False, "Diagnostics root must be an object."
    if payload.get("status") != "ok":
        return False, f"Diagnostics status is {payload.get('status')!r}; expected 'ok'."
    platform_data = payload.get("platform")
    if not isinstance(platform_data, dict) or not all(
        isinstance(platform_data.get(key), str) and platform_data.get(key)
        for key in ("system", "machine", "python_version")
    ):
        return False, "Diagnostics platform metadata is incomplete."
    directories = payload.get("directories_writable")
    if (
        not isinstance(directories, dict)
        or not directories
        or not all(value is True for value in directories.values())
    ):
        return False, "One or more application directories are not writable."

    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        return False, "Diagnostics dependencies are missing."
    required_dependencies = ("ffmpeg_available", "tesseract_available")
    missing_dependencies = [
        name for name in required_dependencies if dependencies.get(name) is not True
    ]
    if missing_dependencies:
        return False, "Required packaged dependencies are unavailable: " + ", ".join(
            missing_dependencies
        )

    languages = dependencies.get("tesseract_languages")
    if not isinstance(languages, list) or not all(
        isinstance(language, str) and language for language in languages
    ):
        return False, "Packaged Tesseract language metadata is missing."
    missing_languages = sorted(REQUIRED_TESSERACT_LANGUAGES.difference(languages))
    if missing_languages:
        return False, "Required Tesseract language models are unavailable: " + ", ".join(
            missing_languages
        )

    if not isinstance(payload.get("manifest_schema_version"), int):
        return False, "Manifest schema version is missing."
    if not isinstance(payload.get("webview_gui"), str) or not payload["webview_gui"]:
        return False, "Webview backend is missing."
    return True, ""


def is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on", "y", "t")


def is_falsy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("0", "false", "no", "off", "n", "f")


def is_ci_environment(env: dict[str, str] | None = None) -> bool:
    target_environment = os.environ if env is None else env
    ci_keys = (
        "CI",
        "GITHUB_ACTIONS",
        "CONTINUOUS_INTEGRATION",
        "TF_BUILD",
        "TRAVIS",
        "BUILDKITE",
    )
    return any(is_truthy(target_environment.get(key)) for key in ci_keys)


def _is_macos_graphical_session(env: dict[str, str]) -> tuple[bool, str]:
    if any(env.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return False, "SSH session detected without local WindowServer console"
    try:
        import ctypes
        import ctypes.util

        core_graphics_path = ctypes.util.find_library("CoreGraphics")
        if not core_graphics_path:
            return False, "CoreGraphics library not found"
        core_graphics = ctypes.cdll.LoadLibrary(core_graphics_path)

        session_function = getattr(core_graphics, "CGSessionCopyCurrentDictionary", None)
        if session_function:
            session_function.restype = ctypes.c_void_p
            session_dictionary = session_function()
            if not session_dictionary:
                return False, "No active WindowServer session found"
            core_foundation_path = ctypes.util.find_library("CoreFoundation")
            if core_foundation_path:
                core_foundation = ctypes.cdll.LoadLibrary(core_foundation_path)
                core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
                core_foundation.CFRelease(session_dictionary)

        main_display = getattr(core_graphics, "CGMainDisplayID", None)
        pixels_wide = getattr(core_graphics, "CGDisplayPixelsWide", None)
        pixels_high = getattr(core_graphics, "CGDisplayPixelsHigh", None)
        if main_display and pixels_wide and pixels_high:
            pixels_wide.restype = ctypes.c_size_t
            pixels_wide.argtypes = [ctypes.c_uint32]
            pixels_high.restype = ctypes.c_size_t
            pixels_high.argtypes = [ctypes.c_uint32]
            display_id = main_display()
            if display_id == 0:
                return False, "No active main display detected"
            width = pixels_wide(display_id)
            height = pixels_high(display_id)
            if width == 0 or height == 0:
                return False, f"Main display has zero dimensions ({width}x{height})"
        return True, "Active macOS Aqua graphical session detected"
    except Exception as exc:
        return False, f"macOS graphical session check failed ({exc})"


def _is_windows_graphical_session() -> tuple[bool, str]:
    try:
        import ctypes

        width = ctypes.windll.user32.GetSystemMetrics(0)
        height = ctypes.windll.user32.GetSystemMetrics(1)
        if width == 0 or height == 0:
            return False, f"Display has zero dimensions ({width}x{height})"

        class _UserObjectFlags(ctypes.Structure):
            _fields_ = [
                ("fInherit", ctypes.c_int),
                ("fReserved", ctypes.c_int),
                ("dwFlags", ctypes.c_ulong),
            ]

        window_station = ctypes.windll.user32.GetProcessWindowStation()
        if window_station:
            flags = _UserObjectFlags()
            required_size = ctypes.c_ulong()
            if ctypes.windll.user32.GetUserObjectInformationW(
                window_station,
                1,
                ctypes.byref(flags),
                ctypes.sizeof(flags),
                ctypes.byref(required_size),
            ) and not (flags.dwFlags & 1):
                return False, "Process window station is not visible (non-interactive session)"
        return True, "Active Windows graphical desktop session detected"
    except Exception as exc:
        return False, f"Windows graphical session check failed ({exc})"


def _is_linux_graphical_session(env: dict[str, str]) -> tuple[bool, str]:
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return True, "Graphical display variable detected (DISPLAY or WAYLAND_DISPLAY)"
    return False, "Neither DISPLAY nor WAYLAND_DISPLAY is set"


def should_run_gui_smoke(
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> tuple[bool, str]:
    target_environment = os.environ if env is None else env
    target_platform = sys.platform if platform is None else platform

    gui_option = target_environment.get("KONSPEKT_GUI_SMOKE")
    if gui_option is not None and gui_option.strip():
        if is_truthy(gui_option):
            return True, "Explicitly enabled via KONSPEKT_GUI_SMOKE"
        if is_falsy(gui_option):
            return False, "Explicitly disabled via KONSPEKT_GUI_SMOKE"

    if is_ci_environment(target_environment):
        return False, "Headless CI environment detected"
    if target_platform == "darwin":
        return _is_macos_graphical_session(target_environment)
    if target_platform == "win32":
        return _is_windows_graphical_session()
    return _is_linux_graphical_session(target_environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        help="Path to a packaged executable or app directory",
    )
    arguments = parser.parse_args(argv)
    executable = find_artifact_executable(arguments.artifact)
    if executable is None:
        print(
            "Error: Built executable not found in dist/. Run build_package.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Running smoke test on {executable}...")
    command = [str(executable), "--diagnostics-json"]
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        clean_path = f"{system_root}\\System32;{system_root}"
    else:
        clean_path = "/usr/bin:/bin:/usr/sbin:/sbin"

    with tempfile.TemporaryDirectory(prefix="konspekt-smoke-") as smoke_root:
        smoke_path = Path(smoke_root)
        environment = os.environ.copy()
        environment["PATH"] = clean_path
        environment.update(
            {
                "KONSPEKT_DATA_DIR": str(smoke_path / "data"),
                "KONSPEKT_CACHE_DIR": str(smoke_path / "cache"),
                "KONSPEKT_LOG_DIR": str(smoke_path / "logs"),
                "KONSPEKT_TEMP_DIR": str(smoke_path / "temp"),
            }
        )
        try:
            diagnostics_process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            print("Smoke test failed: Process timed out.", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"Smoke test failed to execute: {exc}", file=sys.stderr)
            return 1

        if diagnostics_process.returncode != 0:
            print(
                f"Smoke test failed with return code {diagnostics_process.returncode}.",
                file=sys.stderr,
            )
            print(f"Stderr: {diagnostics_process.stderr}", file=sys.stderr)
            return diagnostics_process.returncode

        try:
            payload = json.loads(diagnostics_process.stdout)
        except json.JSONDecodeError as exc:
            print(f"Smoke test failed: Output is not valid JSON ({exc}).", file=sys.stderr)
            print(f"Stdout: {diagnostics_process.stdout}", file=sys.stderr)
            return 1

        valid, reason = validate_diagnostics(payload)
        if not valid:
            print(f"Smoke test failed: {reason}", file=sys.stderr)
            return 1

        run_gui, gui_reason = should_run_gui_smoke(os.environ)
        if run_gui:
            print(f"Running GUI lifecycle smoke test ({gui_reason})...")
            try:
                gui_process = subprocess.run(
                    [str(executable), "--smoke-test-gui"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                print(
                    "GUI smoke test failed: Process timed out after 15 seconds.",
                    file=sys.stderr,
                )
                return 1
            except Exception as exc:
                print(f"GUI smoke test failed to execute: {exc}", file=sys.stderr)
                return 1

            if gui_process.returncode != 0:
                print(
                    f"GUI smoke test failed with return code {gui_process.returncode}.",
                    file=sys.stderr,
                )
                if gui_process.stderr:
                    print(f"Stderr: {gui_process.stderr}", file=sys.stderr)
                if gui_process.stdout:
                    print(f"Stdout: {gui_process.stdout}", file=sys.stderr)
                return gui_process.returncode
            print("GUI lifecycle smoke test passed.")
        else:
            print(
                f"GUI smoke test skipped: {gui_reason}. "
                "Set KONSPEKT_GUI_SMOKE=1 to force GUI lifecycle verification."
            )

    print("Smoke test passed successfully!")
    print(f"Status: {payload.get('status')}")
    print(
        f"Platform: {payload.get('platform', {}).get('system')} "
        f"({payload.get('platform', {}).get('machine')})"
    )
    print(f"Webview GUI: {payload.get('webview_gui')}")
    print(
        "Tesseract languages: "
        + ", ".join(payload.get("dependencies", {}).get("tesseract_languages", []))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
