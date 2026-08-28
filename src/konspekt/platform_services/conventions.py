"""Keyboard shortcuts, font hierarchies, and accessibility conventions."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformKeyboardConventions:
    """Provides platform-accurate shortcut strings and modifier naming."""

    system_override: str | None = None
    target_os: str | None = None

    @property
    def _system(self) -> str:
        return self.target_os or self.system_override or sys.platform

    @property
    def primary_modifier(self) -> str:
        return "⌘" if self._system == "darwin" else "Ctrl"

    def format_shortcut(self, key: str) -> str:
        clean_key = key.strip()
        if self._system == "darwin":
            return f"⌘{clean_key}"
        return f"Ctrl+{clean_key}"


@dataclass(frozen=True)
class PlatformAppearancePreferences:
    """Platform-standard font families, high contrast, and motion preferences."""

    system_override: str | None = None
    target_os: str | None = None
    force_high_contrast: bool | None = None
    force_reduced_motion: bool | None = None

    @property
    def _system(self) -> str:
        return self.target_os or self.system_override or sys.platform

    @property
    def font_family_body(self) -> str:
        if self._system == "darwin":
            return "Helvetica Neue"
        if self._system == "win32":
            return "Segoe UI"
        return "DejaVu Sans"

    @property
    def font_family_heading(self) -> str:
        if self._system == "darwin":
            return "Helvetica Neue"
        if self._system == "win32":
            return "Segoe UI"
        return "DejaVu Sans"

    @property
    def font_family_code(self) -> str:
        if self._system == "darwin":
            return "Menlo"
        if self._system == "win32":
            return "Consolas"
        return "DejaVu Sans Mono"

    @property
    def high_contrast(self) -> bool:
        if self.force_high_contrast is not None:
            return self.force_high_contrast
        return False

    @property
    def reduced_motion(self) -> bool:
        if self.force_reduced_motion is not None:
            return self.force_reduced_motion
        return False
