"""Atomic file I/O operations for safe and resilient file writing."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AtomicIOError(RuntimeError):
    """An atomic file write or replace operation failed."""


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    make_parents: bool = True,
) -> Path:
    """Atomically write text to path using a temporary file in the same directory."""

    target = Path(path)
    if make_parents:
        target.parent.mkdir(parents=True, exist_ok=True)

    prefix = f".{target.name}."
    suffix = ".tmp"
    fd, temp_path_str = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=target.parent)
    temp_path = Path(temp_path_str)

    try:
        with open(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temp_path, target)
        return target
    except BaseException as exc:
        try:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, (OSError, UnicodeError)):
            raise AtomicIOError(f"Не удалось записать файл {target.name}: {exc}") from exc
        raise


def atomic_write_json(
    path: Path | str,
    payload: Any,
    *,
    encoding: str = "utf-8",
    indent: int = 2,
    ensure_ascii: bool = False,
    make_parents: bool = True,
) -> Path:
    """Atomically write JSON data to path using a temporary file in the same directory."""

    try:
        serialized = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
    except (TypeError, ValueError) as exc:
        raise AtomicIOError(f"Не удалось сериализовать JSON для {Path(path).name}: {exc}") from exc

    return atomic_write_text(
        path,
        serialized + "\n",
        encoding=encoding,
        make_parents=make_parents,
    )
