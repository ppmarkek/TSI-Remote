"""Local diagnostic logging and system health diagnostics without leaking user secrets."""

from __future__ import annotations

import platform
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .lecture_manifest import MANIFEST_SCHEMA_VERSION
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
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"[{timestamp}] {area}\n{trace}\n\n")
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
    """Collect non-secret environment and subsystem health metrics for smoke tests and diagnostics."""
    paths = app_paths or PlatformAppPaths()
    locator = PlatformDependencyLocator()

    # Check directory writability
    dirs_health: dict[str, bool] = {}
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
            dirs_health[name] = True
        except OSError:
            dirs_health[name] = False

    ffmpeg_bin = locator.find_ffmpeg()
    tesseract_bin = locator.find_tesseract()
    codex_bin = locator.find_codex()

    return {
        "status": "ok" if all(dirs_health.values()) else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "platform": {
            "system": sys.platform,
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "webview_gui": webview_gui_for_platform(),
        "directories_writable": dirs_health,
        "dependencies": {
            "ffmpeg_available": bool(ffmpeg_bin),
            "tesseract_available": bool(tesseract_bin),
            "codex_available": bool(codex_bin),
        },
    }
