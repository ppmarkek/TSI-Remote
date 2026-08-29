"""Platform abstraction layer providing unified OS paths, secrets, UI conventions, and actions."""

from .browser_auth import (
    BrowserAuthError,
    run_platform_auth_window,
    validate_auth_url,
    webview_gui_for_platform,
)
from .conventions import PlatformAppearancePreferences, PlatformKeyboardConventions
from .dependencies import PlatformDependencyLocator
from .factory import get_platform_services
from .interfaces import (
    AppearancePreferences,
    AppPaths,
    DependencyLocator,
    KeyboardConventions,
    PlatformServices,
    SecretStore,
    SystemActions,
)
from .migration import (
    MigrationResult,
    MigrationStatus,
    find_legacy_source_dir,
    migrate_legacy_data,
)
from .paths import PlatformAppPaths
from .secrets import KeyringSecretStore, SecretStoreError, migrate_legacy_windows_dpapi
from .system_actions import (
    PlatformSystemActions,
    SystemActionError,
    file_manager_command,
)

__all__ = [
    "AppPaths",
    "AppearancePreferences",
    "BrowserAuthError",
    "DependencyLocator",
    "KeyboardConventions",
    "KeyringSecretStore",
    "MigrationResult",
    "MigrationStatus",
    "PlatformAppPaths",
    "PlatformAppearancePreferences",
    "PlatformDependencyLocator",
    "PlatformKeyboardConventions",
    "PlatformServices",
    "PlatformSystemActions",
    "SecretStore",
    "SecretStoreError",
    "SystemActionError",
    "SystemActions",
    "file_manager_command",
    "find_legacy_source_dir",
    "get_platform_services",
    "migrate_legacy_data",
    "migrate_legacy_windows_dpapi",
    "run_platform_auth_window",
    "validate_auth_url",
    "webview_gui_for_platform",
]
