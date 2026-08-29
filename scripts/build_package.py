#!/usr/bin/env python3
"""Cross-platform application packaging using PyInstaller."""

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
        spec_file = packaging_dir / "Konspekt.macos.spec"

    if not spec_file.is_file():
        print(f"Error: Spec file not found: {spec_file}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    print(f"Executing: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
    if result.returncode != 0:
        print("Build failed.", file=sys.stderr)
        return result.returncode

    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        app_bundle = dist_dir / "Konspekt.app"
        if app_bundle.is_dir():
            print(f"Successfully built macOS bundle: {app_bundle}")
            dmg_path = dist_dir / "Konspekt.dmg"
            dmg_path.unlink(missing_ok=True)
            print("Creating macOS DMG installer...")
            dmg_cmd = [
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
            dmg_proc = subprocess.run(dmg_cmd, capture_output=True, text=True, check=False)
            if dmg_proc.returncode == 0 and dmg_path.is_file():
                print(f"Successfully created macOS DMG: {dmg_path}")
                return 0
            print(
                f"Error: Failed to create macOS DMG (exit code {dmg_proc.returncode}): {dmg_proc.stderr.strip()}",
                file=sys.stderr,
            )
            return dmg_proc.returncode if dmg_proc.returncode != 0 else 1
    elif sys.platform == "win32":
        exe_file = dist_dir / "Konspekt" / "Konspekt.exe"
        if exe_file.is_file():
            print(f"Successfully built Windows executable: {exe_file}")
            return 0

    print(f"Build completed in {dist_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
