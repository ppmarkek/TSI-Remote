"""Interfaces and protocols for operating system abstraction services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class AppPaths(Protocol):
    """Platform-specific directory paths for application data, caches, logs, and temp."""

    @property
    def data_dir(self) -> Path: ...

    @property
    def cache_dir(self) -> Path: ...

    @property
    def log_dir(self) -> Path: ...

    @property
    def temp_dir(self) -> Path: ...

    @property
    def library_path(self) -> Path: ...

    @property
    def settings_path(self) -> Path: ...

    @property
    def lectures_dir(self) -> Path: ...

    @property
    def diagnostic_dir(self) -> Path: ...


@runtime_checkable
class SecretStore(Protocol):
    """Secure credential storage interface (Keyring, Keychain, Credential Manager)."""

    def get_secret(self, service: str, account: str) -> str | None: ...

    def set_secret(self, service: str, account: str, secret: str) -> None: ...

    def delete_secret(self, service: str, account: str) -> None: ...


@runtime_checkable
class SystemActions(Protocol):
    """OS actions for file management and URL launching."""

    def open_in_file_manager(self, path: Path) -> None: ...

    def open_url(self, url: str) -> bool: ...


@runtime_checkable
class KeyboardConventions(Protocol):
    """Platform-appropriate modifier keys and shortcut text."""

    @property
    def primary_modifier(self) -> str: ...

    def format_shortcut(self, key: str) -> str: ...


@runtime_checkable
class AppearancePreferences(Protocol):
    """Platform-appropriate typography, accessibility, and motion settings."""

    @property
    def font_family_body(self) -> str: ...

    @property
    def font_family_heading(self) -> str: ...

    @property
    def font_family_code(self) -> str: ...

    @property
    def high_contrast(self) -> bool: ...

    @property
    def reduced_motion(self) -> bool: ...


@runtime_checkable
class DependencyLocator(Protocol):
    """Discovers external binaries (ffmpeg, tesseract, codex) on standard system paths."""

    def find_executable(self, name: str) -> str | None: ...

    def find_ffmpeg(self) -> str | None: ...

    def find_tesseract(self) -> str | None: ...

    def find_codex(self) -> str | None: ...


@dataclass(frozen=True)
class PlatformServices:
    """Aggregated container for all platform-specific services."""

    paths: AppPaths
    secrets: SecretStore
    actions: SystemActions
    keyboard: KeyboardConventions
    appearance: AppearancePreferences
    dependencies: DependencyLocator
