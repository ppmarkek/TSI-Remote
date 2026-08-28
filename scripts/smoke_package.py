#!/usr/bin/env python3
"""Smoke test the built artifact by running non-GUI diagnostics verification."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_artifact_executable() -> Path | None:
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


def main() -> int:
    executable = find_artifact_executable()
    if executable is None:
        print(
            "Error: Built executable not found in dist/. Run build_package.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Running smoke test on {executable}...")
    cmd = [str(executable), "--diagnostics-json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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

    if "status" not in payload or "platform" not in payload:
        print("Smoke test failed: Missing required fields in diagnostics JSON.", file=sys.stderr)
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
