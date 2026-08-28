#!/usr/bin/env python3
"""Smoke test the built artifact by running non-GUI diagnostics verification."""

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
    # Keep the packaged probe self-contained.  CI and local sandboxes may not
    # grant a frozen binary access to the user's normal Application Support,
    # Cache, and Logs roots; diagnostics should still prove the artifact can
    # create all of its own state directories.
    with tempfile.TemporaryDirectory(prefix="konspekt-smoke-") as smoke_root:
        smoke_path = Path(smoke_root)
        environment = os.environ.copy()
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

    print("Smoke test passed successfully!")
    print(f"Status: {payload.get('status')}")
    print(
        f"Platform: {payload.get('platform', {}).get('system')} ({payload.get('platform', {}).get('machine')})"
    )
    print(f"Webview GUI: {payload.get('webview_gui')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
