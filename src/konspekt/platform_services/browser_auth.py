"""Embedded OAuth window support with platform-appropriate webview backends."""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import urlsplit

AUTH_URL_ENV = "KONSPEKT_CHATGPT_AUTH_URL"
ALLOWED_AUTH_HOSTS = frozenset({"auth.openai.com", "chatgpt.com"})


class BrowserAuthError(RuntimeError):
    """The embedded authentication browser could not be opened safely."""


def webview_gui_for_platform(system: str | None = None) -> str:
    """Return the pywebview GUI engine identifier for the given OS platform."""
    target_system = system or sys.platform
    if target_system == "darwin":
        return "cocoa"
    if target_system == "win32":
        return "edgechromium"
    return "gtk"


def validate_auth_url(value: str | None, allowed_hosts: frozenset[str] = ALLOWED_AUTH_HOSTS) -> str:
    """Validate that the OAuth URL uses HTTPS and points strictly to an authorized identity provider."""
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise BrowserAuthError("Invalid authentication URL.") from exc

    hostname = parsed.hostname.casefold() if parsed.hostname else None
    if (
        parsed.scheme.casefold() != "https"
        or hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise BrowserAuthError("Untrusted authentication URL.")
    return candidate


def run_platform_auth_window(
    auth_url: str | None = None,
    system: str | None = None,
    *,
    webview_module: Any = None,
) -> None:
    """Open a sandboxed, private webview for user authentication."""
    candidate = auth_url
    if candidate is None:
        candidate = os.environ.pop(AUTH_URL_ENV, "")
    validated_url = validate_auth_url(candidate)

    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError as exc:
            raise BrowserAuthError("The embedded authentication window is unavailable.") from exc

    target_gui = webview_gui_for_platform(system)
    try:
        webview_module.create_window(
            "Вход в ChatGPT",
            validated_url,
            width=760,
            height=860,
            min_size=(560, 640),
        )
        webview_module.start(
            gui=target_gui,
            debug=False,
            private_mode=True,
        )
    except Exception as exc:
        raise BrowserAuthError(
            f"The embedded authentication window could not be opened: {exc}"
        ) from exc
