"""Platform-aware directory structure using platformdirs with test isolation."""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import platformdirs


@dataclass(frozen=True)
class PlatformAppPaths:
    """Manages application paths across Windows, macOS, and Linux with testing support."""

    app_name: str = "Konspekt"
    app_author: str = "Konspekt"
    test_root: Path | None = None
    system_override: str | None = None

    @property
    def _system(self) -> str:
        return self.system_override or sys.platform

    @property
    def data_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "data"
        if "LOCALAPPDATA" in os.environ:
            return Path(os.environ["LOCALAPPDATA"]) / self.app_name
        if self._system == "win32":
            return Path.home() / "AppData" / "Local" / self.app_name
        return Path(platformdirs.user_data_dir(self.app_name, self.app_author))

    @property
    def cache_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "cache"
        return Path(platformdirs.user_cache_dir(self.app_name, self.app_author))

    @property
    def log_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "logs"
        return Path(platformdirs.user_log_dir(self.app_name, self.app_author))

    @property
    def temp_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "temp"
        return Path(tempfile.gettempdir()) / self.app_name

    @property
    def library_path(self) -> Path:
        return self.data_dir / "library.json"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def lectures_dir(self) -> Path:
        return self.data_dir / "lectures"

    @property
    def diagnostic_dir(self) -> Path:
        return self.log_dir / "diagnostics"

    def ensure_directories(self) -> None:
        """Create standard application directories if they do not exist."""
        for directory in (
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.temp_dir,
            self.lectures_dir,
            self.diagnostic_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
