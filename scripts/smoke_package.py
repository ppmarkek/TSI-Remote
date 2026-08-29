#!/usr/bin/env python3
"""Smoke test the built artifact by running non-GUI diagnostics and conditional GUI verification."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_artifact_executable(artifact: Path | None = None) -> Path | None:
    if artifact is not None:
        candidate = artifact.expanduser().resolve()
        if candidate.is_file():
            return candidate
        # Accept an app/extraction directory as a convenience for CI callers.
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
        binary_alt = dist_dir / "Konspekt" / "Konspekt"
        if binary_alt.is_file():
            return binary_alt
    elif sys.platform == "win32":
        exe = dist_dir / "Konspekt" / "Konspekt.exe"
        if exe.is_file():
            return exe
    else:
        binary = dist_dir / "Konspekt" / "Konspekt"
        if binary.is_file():
            return binary
    return None


def validate_diagnostics(payload: object) -> tuple[bool, str]:
    """Validate the complete runtime contract, not just JSON field presence."""
    if not isinstance(payload, dict):
        return False, "Diagnostics root must be an object."
    if payload.get("status") != "ok":
        return False, f"Diagnostics status is {payload.get('status')!r}; expected 'ok'."
    platform = payload.get("platform")
    if not isinstance(platform, dict) or not all(
        isinstance(platform.get(key), str) and platform.get(key)
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
    required = ("ffmpeg_available", "tesseract_available")
    missing = [name for name in required if dependencies.get(name) is not True]
    if missing:
        return False, "Required packaged dependencies are unavailable: " + ", ".join(missing)
    if not isinstance(payload.get("manifest_schema_version"), int):
        return False, "Manifest schema version is missing."
    if not isinstance(payload.get("webview_gui"), str) or not payload["webview_gui"]:
        return False, "Webview backend is missing."
    return True, ""


def is_truthy(value: str | None) -> bool:
    """Return True if string represents a truthy configuration value."""
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on", "y", "t")


def is_falsy(value: str | None) -> bool:
    """Return True if string represents an explicit falsy configuration value."""
    if value is None:
        return False
    return value.strip().lower() in ("0", "false", "no", "off", "n", "f")


def is_ci_environment(env: dict[str, str] | None = None) -> bool:
    """Check whether execution is inside a known CI/CD environment."""
    target_env = os.environ if env is None else env
    ci_keys = ("CI", "GITHUB_ACTIONS", "CONTINUOUS_INTEGRATION", "TF_BUILD", "TRAVIS", "BUILDKITE")
    return any(is_truthy(target_env.get(k)) for k in ci_keys)


def _is_macos_graphical_session(env: dict[str, str]) -> tuple[bool, str]:
    """Detect whether a macOS session has an active local WindowServer console."""
    if any(env.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return False, "SSH session detected without local WindowServer console"
    try:
        import ctypes
        import ctypes.util

        cg_path = ctypes.util.find_library("CoreGraphics")
        if not cg_path:
            return False, "CoreGraphics library not found"
        cg = ctypes.cdll.LoadLibrary(cg_path)

        cg_session = getattr(cg, "CGSessionCopyCurrentDictionary", None)
        if cg_session:
            cg_session.restype = ctypes.c_void_p
            cf_dict = cg_session()
            if not cf_dict:
                return False, "No active WindowServer session found"
            cf_path = ctypes.util.find_library("CoreFoundation")
            if cf_path:
                cf = ctypes.cdll.LoadLibrary(cf_path)
                cf.CFRelease.argtypes = [ctypes.c_void_p]
                cf.CFRelease(cf_dict)

        cg_main_display = getattr(cg, "CGMainDisplayID", None)
        cg_pixels_wide = getattr(cg, "CGDisplayPixelsWide", None)
        cg_pixels_high = getattr(cg, "CGDisplayPixelsHigh", None)
        if cg_main_display and cg_pixels_wide and cg_pixels_high:
            cg_pixels_wide.restype = ctypes.c_size_t
            cg_pixels_wide.argtypes = [ctypes.c_uint32]
            cg_pixels_high.restype = ctypes.c_size_t
            cg_pixels_high.argtypes = [ctypes.c_uint32]
            display_id = cg_main_display()
            if display_id == 0:
                return False, "No active main display detected"
            width = cg_pixels_wide(display_id)
            height = cg_pixels_high(display_id)
            if width == 0 or height == 0:
                return False, f"Main display has zero dimensions ({width}x{height})"

        return True, "Active macOS Aqua graphical session detected"
    except Exception as exc:
        return False, f"macOS graphical session check failed ({exc})"


def _is_windows_graphical_session() -> tuple[bool, str]:
    """Detect whether a Windows session has an active interactive desktop."""
    try:
        import ctypes

        width = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
        height = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        if width == 0 or height == 0:
            return False, f"Display has zero dimensions ({width}x{height})"

        class _UserObjectFlags(ctypes.Structure):
            _fields_ = [
                ("fInherit", ctypes.c_int),
                ("fReserved", ctypes.c_int),
                ("dwFlags", ctypes.c_ulong),
            ]

        hwinsta = ctypes.windll.user32.GetProcessWindowStation()
        if hwinsta:
            flags = _UserObjectFlags()
            needed = ctypes.c_ulong()
            if ctypes.windll.user32.GetUserObjectInformationW(
                hwinsta, 1, ctypes.byref(flags), ctypes.sizeof(flags), ctypes.byref(needed)
            ):
                if not (flags.dwFlags & 1):
                    return False, "Process window station is not visible (non-interactive session)"

        return True, "Active Windows graphical desktop session detected"
    except Exception as exc:
        return False, f"Windows graphical session check failed ({exc})"


def _is_linux_graphical_session(env: dict[str, str]) -> tuple[bool, str]:
    """Detect whether a Linux/Unix session has a graphical display variable configured."""
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return True, "Graphical display variable detected (DISPLAY or WAYLAND_DISPLAY)"
    return False, "Neither DISPLAY nor WAYLAND_DISPLAY is set"


def should_run_gui_smoke(
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> tuple[bool, str]:
    """Determine whether the GUI smoke subprocess should run based on opt-in or environment."""
    target_env = os.environ if env is None else env
    target_platform = sys.platform if platform is None else platform

    gui_opt = target_env.get("KONSPEKT_GUI_SMOKE")
    if gui_opt is not None and gui_opt.strip():
        if is_truthy(gui_opt):
            return True, "Explicitly enabled via KONSPEKT_GUI_SMOKE"
        if is_falsy(gui_opt):
            return False, "Explicitly disabled via KONSPEKT_GUI_SMOKE"

    if is_ci_environment(target_env):
        return False, "Headless CI environment detected"

    if target_platform == "darwin":
        return _is_macos_graphical_session(target_env)
    if target_platform == "win32":
        return _is_windows_graphical_session()
    return _is_linux_graphical_session(target_env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact", type=Path, help="Path to a packaged executable or app directory"
    )
    args = parser.parse_args(argv)
    executable = find_artifact_executable(args.artifact)
    if executable is None:
        print(
            "Error: Built executable not found in dist/. Run build_package.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Running smoke test on {executable}...")
    cmd = [str(executable), "--diagnostics-json"]
    # Sanitize PATH so bundled tools are tested without reliance on developer PATH
    if sys.platform == "win32":
        sys_root = os.environ.get("SystemRoot", r"C:\Windows")
        clean_path = f"{sys_root}\\System32;{sys_root}"
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
            proc = subprocess.run(
                cmd,
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

        if proc.returncode != 0:
            print(f"Smoke test failed with return code {proc.returncode}.", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            return proc.returncode

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            print(f"Smoke test failed: Output is not valid JSON ({exc}).", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            return 1

        valid, reason = validate_diagnostics(payload)
        if not valid:
            print(f"Smoke test failed: {reason}", file=sys.stderr)
            return 1

        # Quick GUI startup / lifecycle smoke test
        run_gui, gui_reason = should_run_gui_smoke(os.environ)
        if run_gui:
            print(f"Running GUI lifecycle smoke test ({gui_reason})...")
            try:
                gui_cmd = [str(executable), "--smoke-test-gui"]
                gui_proc = subprocess.run(
                    gui_cmd,
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

            if gui_proc.returncode != 0:
                print(
                    f"GUI smoke test failed with return code {gui_proc.returncode}.",
                    file=sys.stderr,
                )
                if gui_proc.stderr:
                    print(f"Stderr: {gui_proc.stderr}", file=sys.stderr)
                if gui_proc.stdout:
                    print(f"Stdout: {gui_proc.stdout}", file=sys.stderr)
                return gui_proc.returncode
            print("GUI lifecycle smoke test passed.")
        else:
            print(
                f"GUI smoke test skipped: {gui_reason}. "
                "Set KONSPEKT_GUI_SMOKE=1 to force GUI lifecycle verification."
            )

    print("Smoke test passed successfully!")
    print(f"Status: {payload.get('status')}")
    print(
        f"Platform: {payload.get('platform', {}).get('system')} ({payload.get('platform', {}).get('machine')})"
    )
    print(f"Webview GUI: {payload.get('webview_gui')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
