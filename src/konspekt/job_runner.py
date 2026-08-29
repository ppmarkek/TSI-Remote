"""Durable background job runner with cancellation tokens and subprocess termination."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class JobCancelledError(RuntimeError):
    """The background task was explicitly cancelled by the user or system."""


class JobEventType(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobEvent:
    event_type: JobEventType
    percent: int = 0
    message: str = ""
    result: Any = None
    error: str | None = None


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelledError("Операция была отменена пользователем.")

    def register(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._cancelled.is_set():
                callback()
                return
            self._callbacks.append(callback)


def terminate_process_tree(
    process: subprocess.Popen[Any], grace_period_seconds: float = 2.0
) -> None:
    """Terminate a subprocess gracefully, falling back to kill if it does not exit."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        return
    deadline = time.monotonic() + grace_period_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.05)
    try:
        process.kill()
    except OSError:
        pass


class JobRunner:
    """Manages execution of background operations with orderly events and safe cancellation."""

    def __init__(self) -> None:
        self._active_threads: list[threading.Thread] = []
        self._active_tokens: list[CancellationToken] = []
        self._lock = threading.Lock()

    def run_job(
        self,
        target_fn: Callable[[CancellationToken, Callable[[int, str], None]], Any],
        on_event: Callable[[JobEvent], None],
        token: CancellationToken | None = None,
    ) -> CancellationToken:
        cancellation_token = token or CancellationToken()

        def worker() -> None:
            on_event(JobEvent(event_type=JobEventType.STARTED, percent=0, message="Запуск задачи…"))

            def progress_callback(percent: int, message: str) -> None:
                if cancellation_token.is_cancelled:
                    raise JobCancelledError("Операция отменена.")
                on_event(
                    JobEvent(
                        event_type=JobEventType.PROGRESS,
                        percent=percent,
                        message=message,
                    )
                )

            try:
                result = target_fn(cancellation_token, progress_callback)
                if cancellation_token.is_cancelled:
                    on_event(
                        JobEvent(event_type=JobEventType.CANCELLED, message="Операция отменена.")
                    )
                else:
                    on_event(
                        JobEvent(
                            event_type=JobEventType.COMPLETED,
                            percent=100,
                            message="Задача успешно завершена.",
                            result=result,
                        )
                    )
            except JobCancelledError:
                on_event(JobEvent(event_type=JobEventType.CANCELLED, message="Операция отменена."))
            except Exception as exc:
                on_event(
                    JobEvent(
                        event_type=JobEventType.FAILED,
                        error=str(exc),
                        message=f"Ошибка выполнения: {exc}",
                    )
                )
            finally:
                with self._lock:
                    if thread in self._active_threads:
                        self._active_threads.remove(thread)
                    if cancellation_token in self._active_tokens:
                        self._active_tokens.remove(cancellation_token)

        thread = threading.Thread(target=worker, daemon=True, name="konspekt-job")
        with self._lock:
            self._active_threads.append(thread)
            self._active_tokens.append(cancellation_token)
        thread.start()
        return cancellation_token

    def cancel_all(self) -> None:
        with self._lock:
            tokens = list(self._active_tokens)
        for token in tokens:
            token.cancel()

    def shutdown(self, timeout_seconds: float = 3.0) -> None:
        self.cancel_all()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                alive = [t for t in self._active_threads if t.is_alive()]
            if not alive:
                return
            time.sleep(0.05)
