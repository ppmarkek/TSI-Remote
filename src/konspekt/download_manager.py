"""Resumable, verifiable media downloads with disk space checking and cancellation support."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from .job_runner import CancellationToken, JobCancelledError

ProgressCallback = Callable[[int, str], None]


class DownloadError(RuntimeError):
    """A media track could not be downloaded completely or safely."""


def download_file_resumable(
    url: str,
    target_path: Path,
    *,
    token: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
    session: Any | None = None,
    chunk_size: int = 65536,
) -> Path:
    """Download a file with HTTP Range resume support, atomic activation, and cancellation."""

    if target_path.is_file() and target_path.stat().st_size > 0:
        if progress:
            progress(100, f"{target_path.name} уже скачан.")
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = target_path.with_name(f"{target_path.name}.part")
    existing_bytes = part_path.stat().st_size if part_path.is_file() else 0

    headers: dict[str, str] = {}
    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"

    owns_client = session is None
    client = session or requests.Session()

    try:
        if token:
            token.check_cancelled()

        try:
            response = client.get(url, headers=headers, stream=True, timeout=(15, 60))
        except (requests.RequestException, OSError) as exc:
            raise DownloadError(f"Не удалось подключиться к серверу для загрузки: {exc}") from exc

        try:
            if response.status_code == 416:
                # Range not satisfiable; likely existing_bytes matches or exceeds total size
                part_path.unlink(missing_ok=True)
                existing_bytes = 0
                response.close()
                response = client.get(url, stream=True, timeout=(15, 60))

            if response.status_code not in (200, 206):
                raise DownloadError(
                    f"Сервер вернул ошибку {response.status_code} при скачивании {target_path.name}."
                )

            is_resumed = response.status_code == 206
            file_mode = "ab" if is_resumed and existing_bytes > 0 else "wb"
            start_offset = existing_bytes if is_resumed else 0

            # Determine total length
            content_length = response.headers.get("Content-Length")
            total_bytes: int | None = None
            if content_length and content_length.isdigit():
                total_bytes = int(content_length) + start_offset

            # Check disk space if total_bytes is known
            if total_bytes:
                try:
                    usage = shutil.disk_usage(target_path.parent)
                    required = total_bytes - start_offset + 10 * 1024 * 1024  # 10MB safety margin
                    if usage.free < required:
                        raise DownloadError("Недостаточно свободного места на диске для загрузки.")
                except OSError:
                    pass

            downloaded_bytes = start_offset
            with part_path.open(file_mode) as file_out:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if token and token.is_cancelled:
                        raise JobCancelledError("Загрузка отменена.")
                    if chunk:
                        file_out.write(chunk)
                        downloaded_bytes += len(chunk)
                        if progress and total_bytes and total_bytes > 0:
                            percent = min(99, int((downloaded_bytes / total_bytes) * 100))
                            progress(percent, f"Скачиваем {target_path.name}: {percent}%")

            if total_bytes and downloaded_bytes < total_bytes:
                raise DownloadError("Загрузка файла прервана до получения полного объёма.")

            os.replace(part_path, target_path)
            if progress:
                progress(100, f"{target_path.name} успешно скачан.")
            return target_path

        finally:
            response.close()

    except JobCancelledError:
        raise
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"Сбой при скачивании {target_path.name}: {exc}") from exc
    finally:
        if owns_client and hasattr(client, "close"):
            client.close()
