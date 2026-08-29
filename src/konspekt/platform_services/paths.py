"""Platform-aware directory structure with deterministic test overrides."""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformAppPaths:
    """Manage application paths across Windows, macOS, and Linux.

    ``system_override`` is a genuine target-platform override rather than a
    cosmetic flag. This makes cross-platform tests deterministic even when a
    macOS path is evaluated on Windows or Linux, and avoids consulting host-OS
    path rules by mistake.
    """

    app_name: str = "Konspekt"
    app_author: str = "Konspekt"
    test_root: Path | None = None
    system_override: str | None = None

    @property
    def _system(self) -> str:
        value = (self.system_override or sys.platform).lower()
        if value.startswith("win"):
            return "win32"
        if value == "darwin":
            return "darwin"
        return "linux"

    @property
    def data_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "data"
        configured = os.environ.get("KONSPEKT_DATA_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()

        # LOCALAPPDATA is also an explicit isolation hook used by legacy tests
        # and migrations. Honor it when no target platform was forced, but do
        # not let the host environment override an explicit Darwin/Linux test.
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata and (self.system_override is None or self._system == "win32"):
            return Path(local_appdata).expanduser() / self.app_name

        if self._system == "darwin":
            return Path.home() / "Library" / "Application Support" / self.app_name
        if self._system == "win32":
            return self._windows_local_appdata() / self.app_name
        xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
        root = Path(xdg_data).expanduser() if xdg_data else Path.home() / ".local" / "share"
        return root / self.app_name

    @property
    def cache_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "cache"
        configured = os.environ.get("KONSPEKT_CACHE_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
        if self._system == "darwin":
            return Path.home() / "Library" / "Caches" / self.app_name
        if self._system == "win32":
            return self._windows_local_appdata() / self.app_name / "Cache"
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
        root = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
        return root / self.app_name

    @property
    def log_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "logs"
        configured = os.environ.get("KONSPEKT_LOG_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
        if self._system == "darwin":
            return Path.home() / "Library" / "Logs" / self.app_name
        if self._system == "win32":
            return self._windows_local_appdata() / self.app_name / "Logs"
        xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
        root = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
        return root / self.app_name / "log"

    @property
    def temp_dir(self) -> Path:
        if self.test_root is not None:
            return self.test_root / "temp"
        configured = os.environ.get("KONSPEKT_TEMP_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
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

    @staticmethod
    def _windows_local_appdata() -> Path:
        configured = os.environ.get("LOCALAPPDATA", "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path.home() / "AppData" / "Local"
