"""Local diagnostic logging and system health diagnostics without leaking user secrets."""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .lecture_manifest import MANIFEST_SCHEMA_VERSION
from .outbound_context import redact_sensitive_strings
from .platform_services import (
    PlatformAppPaths,
    PlatformDependencyLocator,
    webview_gui_for_platform,
)

MAX_LOG_BYTES = 1_000_000


def diagnostic_log_path() -> Path:
    paths = PlatformAppPaths()
    return paths.log_dir / "konspekt.log"


def record_exception(area: str, error: BaseException) -> Path | None:
    """Append a traceback without request bodies, headers, URLs, or secrets."""

    path = diagnostic_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        trace = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        ).rstrip()
        safe_area = redact_sensitive_strings(str(area))
        safe_trace = redact_sensitive_strings(trace)
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"[{timestamp}] {safe_area}\n{safe_trace}\n\n")
    except OSError:
        return None
    return path


def _rotate_if_needed(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < MAX_LOG_BYTES:
        return
    previous = path.with_suffix(".previous.log")
    previous.unlink(missing_ok=True)
    path.replace(previous)


def collect_system_diagnostics(app_paths: PlatformAppPaths | None = None) -> dict[str, Any]:
    """Collect non-secret environment and subsystem health metrics."""

    paths = app_paths or PlatformAppPaths()
    locator = PlatformDependencyLocator()

    directories_health: dict[str, bool] = {}
    for name, directory in (
        ("data_dir", paths.data_dir),
        ("cache_dir", paths.cache_dir),
        ("log_dir", paths.log_dir),
        ("temp_dir", paths.temp_dir),
    ):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            test_file = directory / ".healthcheck.tmp"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            directories_health[name] = True
        except OSError:
            directories_health[name] = False

    ffmpeg_binary = locator.find_ffmpeg()
    tesseract_binary = locator.find_tesseract()
    codex_binary = locator.find_codex()
    tesseract_languages = _installed_tesseract_languages(tesseract_binary)

    return {
        "status": "ok" if all(directories_health.values()) else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": {
            "system": sys.platform,
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "webview_gui": webview_gui_for_platform(),
        "directories_writable": directories_health,
        "dependencies": {
            "ffmpeg_available": bool(ffmpeg_binary),
            "tesseract_available": bool(tesseract_binary),
            "tesseract_languages": tesseract_languages,
            "codex_available": bool(codex_binary),
        },
    }


def _installed_tesseract_languages(executable: str | None) -> list[str]:
    """Read language model names from the tessdata directory used by Tesseract."""

    if not executable:
        return []
    executable_root = Path(executable).resolve().parent
    candidates = (
        executable_root / "tessdata",
        executable_root.parent / "share" / "tessdata",
    )
    for directory in candidates:
        if not directory.is_dir():
            continue
        languages = sorted(
            path.stem
            for path in directory.glob("*.traineddata")
            if path.is_file() and path.stat().st_size > 0
        )
        if languages:
            return languages
    return []
