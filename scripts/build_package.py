#!/usr/bin/env python3
"""Build and validate the native package for the current platform."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    print(f"Building Konspekt on {sys.platform}...")
    packaging_dir = PROJECT_ROOT / "packaging"
    if sys.platform == "darwin":
        spec_file = packaging_dir / "Konspekt.macos.spec"
    elif sys.platform == "win32":
        spec_file = packaging_dir / "Konspekt.windows.spec"
    else:
        print(f"Error: unsupported packaging platform: {sys.platform}", file=sys.stderr)
        return 1

    if not spec_file.is_file():
        print(f"Error: spec file not found: {spec_file}", file=sys.stderr)
        return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]
    print(f"Executing: {' '.join(command)}")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), env=environment, check=False)
    if result.returncode != 0:
        print("Build failed.", file=sys.stderr)
        return result.returncode

    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        app_bundle = dist_dir / "Konspekt.app"
        if not app_bundle.is_dir():
            print(f"Error: expected macOS app was not created: {app_bundle}", file=sys.stderr)
            return 1

        dmg_path = dist_dir / "Konspekt.dmg"
        dmg_path.unlink(missing_ok=True)
        dmg_command = [
            "hdiutil",
            "create",
            "-volname",
            "Konspekt",
            "-srcfolder",
            str(app_bundle),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
        dmg_result = subprocess.run(
            dmg_command,
            capture_output=True,
            text=True,
            check=False,
        )
        if dmg_result.returncode != 0 or not dmg_path.is_file() or dmg_path.stat().st_size <= 0:
            print(
                "Error: failed to create a non-empty macOS DMG "
                f"(exit code {dmg_result.returncode}): {dmg_result.stderr.strip()}",
                file=sys.stderr,
            )
            return dmg_result.returncode or 1
        print(f"Successfully built macOS bundle: {app_bundle}")
        print(f"Successfully created macOS DMG: {dmg_path}")
        return 0

    executable = dist_dir / "Konspekt" / "Konspekt.exe"
    if not executable.is_file() or executable.stat().st_size <= 0:
        print(f"Error: expected Windows executable was not created: {executable}", file=sys.stderr)
        return 1
    print(f"Successfully built Windows executable: {executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
