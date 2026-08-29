"""Background job runner with cooperative cancellation and process-tree cleanup."""

from __future__ import annotations

import os
import signal
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
            callbacks = tuple(self._callbacks)
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # Cancellation must continue even if one cleanup callback is
                # already stale or its resource has closed itself.
                pass

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelledError("Операция была отменена пользователем.")

    def register(self, callback: Callable[[], None]) -> None:
        call_now = False
        with self._lock:
            if self._cancelled.is_set():
                call_now = True
            else:
                self._callbacks.append(callback)
        # Do not execute arbitrary cleanup code while holding the token lock.
        if call_now:
            try:
                callback()
            except Exception:
                pass


def terminate_process_tree(
    process: subprocess.Popen[Any], grace_period_seconds: float = 2.0
) -> None:
    """Terminate a process and every descendant on Windows, macOS, and Linux.

    Existing call sites do not create dedicated process groups, so this helper
    discovers descendants explicitly.  Windows delegates to ``taskkill /T``;
    POSIX systems snapshot the parent/child table with ``ps`` and signal the
    deepest descendants before the root process.
    """

    if process.poll() is not None:
        return
    if os.name == "nt":
        _terminate_windows_tree(process, grace_period_seconds)
    else:
        _terminate_posix_tree(process, grace_period_seconds)


def _terminate_windows_tree(
    process: subprocess.Popen[Any], grace_period_seconds: float
) -> None:
    pid = process.pid
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1.0, grace_period_seconds),
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.terminate()
        except OSError:
            pass

    if _wait_for_root_exit(process, grace_period_seconds):
        return

    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1.0, grace_period_seconds),
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
    _wait_for_root_exit(process, max(0.2, grace_period_seconds))


def _terminate_posix_tree(
    process: subprocess.Popen[Any], grace_period_seconds: float
) -> None:
    descendants = _posix_descendant_pids(process.pid)
    targets = [*descendants, process.pid]
    _signal_pids(targets, signal.SIGTERM)

    deadline = time.monotonic() + max(0.0, grace_period_seconds)
    while time.monotonic() < deadline:
        process.poll()
        if not any(_pid_exists(pid) for pid in targets):
            return
        time.sleep(0.05)

    _signal_pids(targets, signal.SIGKILL)
    _wait_for_root_exit(process, max(0.2, grace_period_seconds))


def _posix_descendant_pids(root_pid: int) -> list[int]:
    """Return descendants in deepest-first order from one process-table snapshot."""

    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    children_by_parent: dict[int, list[int]] = {}
    for raw_line in result.stdout.splitlines():
        fields = raw_line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = (int(fields[0]), int(fields[1]))
        except ValueError:
            continue
        children_by_parent.setdefault(parent, []).append(pid)

    ordered: list[int] = []
    visited: set[int] = set()

    def visit(parent: int) -> None:
        for child in children_by_parent.get(parent, ()):
            if child in visited:
                continue
            visited.add(child)
            visit(child)
            ordered.append(child)

    visit(root_pid)
    return ordered


def _signal_pids(pids: list[int], sig: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_root_exit(process: subprocess.Popen[Any], timeout_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return process.poll() is not None


class JobRunner:
    """Manage background operations with orderly events and safe cancellation."""

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
            tokens = tuple(self._active_tokens)
        for token in tokens:
            token.cancel()

    def shutdown(self, timeout_seconds: float = 3.0) -> None:
        self.cancel_all()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._lock:
                alive = [thread for thread in self._active_threads if thread.is_alive()]
            if not alive:
                return
            time.sleep(0.05)
