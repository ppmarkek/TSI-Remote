"""Colour the native Windows frame while keeping all OS window behaviours."""

from __future__ import annotations

import sys
import tkinter as tk


def _colorref(value: str) -> int:
    """Convert #RRGGBB to the COLORREF byte order used by DWM."""
    red, green, blue = (int(value[index : index + 2], 16) for index in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def style_native_window(window: tk.Tk | tk.Toplevel, palette: dict[str, str]) -> None:
    """Use supported DWM attributes, falling back to the ordinary system frame.

    Windows 11 accepts caption/text colours and rounded corners. Windows 10
    keeps its native light frame. Never remove the caption or intercept window
    messages: resizing, Snap, Alt+F4, the taskbar and high contrast stay native.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT: Tk's wrapper HWND
    except (OSError, AttributeError, tk.TclError):
        return
    style_native_hwnd(
        hwnd,
        caption=palette["sidebar"],
        text=palette["muted"],
        border=palette["line"],
    )


def style_native_hwnd(
    hwnd: int | None,
    *,
    caption: str,
    text: str,
    border: str,
    dark: bool = False,
) -> None:
    """Apply caption colours to a top-level window handle (no-op in high contrast)."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class HighContrast(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwFlags", wintypes.DWORD),
                ("lpszDefaultScheme", wintypes.LPWSTR),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        contrast = HighContrast()
        contrast.cbSize = ctypes.sizeof(contrast)
        if user32.SystemParametersInfoW(0x0042, contrast.cbSize, ctypes.byref(contrast), 0):
            if contrast.dwFlags & 1:  # HCF_HIGHCONTRASTON
                return

        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        attributes = {
            20: 1 if dark else 0,  # DWMWA_USE_IMMERSIVE_DARK_MODE
            33: 2,  # DWMWA_WINDOW_CORNER_PREFERENCE: DWMWCP_ROUND
            34: _colorref(border),  # DWMWA_BORDER_COLOR
            35: _colorref(caption),  # DWMWA_CAPTION_COLOR
            36: _colorref(text),  # DWMWA_TEXT_COLOR
        }
        for attribute, number in attributes.items():
            value = wintypes.DWORD(number)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError, ValueError):
        # Unsupported DWM attributes fail harmlessly on older Windows builds.
        return


def system_prefers_dark() -> bool:
    """Whether the OS asks applications for a dark appearance."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except (OSError, ValueError):
            return False
    if sys.platform == "darwin":
        import subprocess

        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.stdout.strip().lower() == "dark"
    return False
