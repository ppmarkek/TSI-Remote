"""Locates external binaries across standard system paths on macOS, Windows, and Linux."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformDependencyLocator:
    """Discovers binaries by checking system-specific common directories and PATH."""

    system_override: str | None = None
    extra_paths: tuple[Path, ...] = ()

    @property
    def _system(self) -> str:
        return self.system_override or sys.platform

    def find_executable(self, name: str) -> str | None:
        """Find the full path to an executable name."""
        candidates = self._candidate_paths(name)
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

        found = shutil.which(name)
        if found:
            return found

        if self._system == "win32" and not name.lower().endswith((".exe", ".cmd", ".bat")):
            for ext in (".exe", ".cmd", ".bat"):
                found_ext = shutil.which(f"{name}{ext}")
                if found_ext:
                    return found_ext
        return None

    def find_ffmpeg(self) -> str | None:
        found = self.find_executable("ffmpeg")
        if found:
            return found
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and Path(exe).is_file():
                return exe
        except ImportError:
            pass
        return None

    def find_tesseract(self) -> str | None:
        return self.find_executable("tesseract")

    def find_codex(self) -> str | None:
        return self.find_executable("codex")

    def _candidate_paths(self, name: str) -> list[Path]:
        paths: list[Path] = [Path(p) for p in self.extra_paths]
        home = Path.home()

        if self._system == "darwin":
            paths.extend(
                [
                    Path("/opt/homebrew/bin"),
                    Path("/usr/local/bin"),
                    home / ".local/bin",
                    home / ".cargo/bin",
                ]
            )
        elif self._system == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            program_files = os.environ.get("ProgramFiles")
            program_files_x86 = os.environ.get("ProgramFiles(x86)")
            if local_app_data:
                paths.append(Path(local_app_data) / "Programs")
            if program_files:
                paths.append(Path(program_files))
            if program_files_x86:
                paths.append(Path(program_files_x86))
            paths.append(home / ".cargo/bin")
        else:
            paths.extend(
                [
                    Path("/usr/local/bin"),
                    Path("/usr/bin"),
                    home / ".local/bin",
                    home / ".cargo/bin",
                ]
            )

        targets: list[Path] = []
        extensions = ("", ".exe", ".cmd", ".bat") if self._system == "win32" else ("",)
        for base in paths:
            for ext in extensions:
                targets.append(base / f"{name}{ext}")
        return targets
