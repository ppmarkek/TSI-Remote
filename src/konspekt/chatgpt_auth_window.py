"""Private pywebview process used only for the official ChatGPT login URL."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .platform_services import (
    BrowserAuthError,
    run_platform_auth_window,
    validate_auth_url,
)

AUTH_URL_ENV = "KONSPEKT_CHATGPT_AUTH_URL"


class ChatGPTAuthWindowError(RuntimeError):
    """The internal authentication window could not be opened safely."""


def run_auth_window(
    auth_url: str | None = None,
    *,
    webview_module: Any = None,
    system: str | None = None,
) -> None:
    """Display the OAuth page without exposing browser state to Konspekt."""
    try:
        run_platform_auth_window(
            auth_url=auth_url,
            system=system,
            webview_module=webview_module,
        )
    except BrowserAuthError as exc:
        raise ChatGPTAuthWindowError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        return 2
    try:
        run_auth_window()
    except ChatGPTAuthWindowError:
        return 1
    return 0


def _validated_auth_url(value: str | None) -> str:
    try:
        return validate_auth_url(value)
    except BrowserAuthError as exc:
        raise ChatGPTAuthWindowError(str(exc)) from exc
