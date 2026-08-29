"""System integration actions for file managers and default browser navigation."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class SystemActionError(RuntimeError):
    """An OS action could not be performed."""


def file_manager_command(path: Path, system: str | None = None) -> list[str]:
    """Return the command arguments to reveal or open a path in the platform file manager."""
    target_system = system or sys.platform
    target_path = str(path.resolve())
    if target_system == "darwin":
        return ["open", target_path]
    if target_system == "win32":
        return ["explorer.exe", target_path]
    return ["xdg-open", target_path]


@dataclass(frozen=True)
class PlatformSystemActions:
    """Invokes native platform actions safely without shell interpolation."""

    system_override: str | None = None

    @property
    def _system(self) -> str:
        return self.system_override or sys.platform

    def open_in_file_manager(self, path: Path) -> None:
        if not path.exists():
            raise SystemActionError(f"Путь не существует: {path}")

        if self._system == "win32" and hasattr(os, "startfile"):
            try:
                os.startfile(str(path))
                return
            except OSError as exc:
                raise SystemActionError("Не удалось открыть проводник Windows.") from exc

        cmd = file_manager_command(path, self._system)
        try:
            subprocess.Popen(cmd, shell=False)
        except OSError as exc:
            raise SystemActionError(f"Не удалось открыть менеджер файлов: {exc}") from exc

    def open_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise SystemActionError(f"Недопустимый протокол URL: {parsed.scheme}")
        try:
            return bool(webbrowser.open_new_tab(url))
        except Exception as exc:
            raise SystemActionError("Не удалось открыть браузер по умолчанию.") from exc
