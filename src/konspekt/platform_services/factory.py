"""Factory for assembling unified platform services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conventions import PlatformAppearancePreferences, PlatformKeyboardConventions
from .dependencies import PlatformDependencyLocator
from .interfaces import PlatformServices
from .paths import PlatformAppPaths
from .secrets import KeyringSecretStore
from .system_actions import PlatformSystemActions


def get_platform_services(
    *,
    test_root: Path | None = None,
    system_override: str | None = None,
    keyring_backend: Any = None,
) -> PlatformServices:
    """Instantiate and return platform services configured for the current or test environment."""
    paths = PlatformAppPaths(test_root=test_root, system_override=system_override)
    secrets = KeyringSecretStore(backend=keyring_backend)
    actions = PlatformSystemActions(system_override=system_override)
    keyboard = PlatformKeyboardConventions(system_override=system_override)
    appearance = PlatformAppearancePreferences(system_override=system_override)
    dependencies = PlatformDependencyLocator(system_override=system_override)

    return PlatformServices(
        paths=paths,
        secrets=secrets,
        actions=actions,
        keyboard=keyboard,
        appearance=appearance,
        dependencies=dependencies,
    )
