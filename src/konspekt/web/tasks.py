"""Background task queue for the desktop UI.

Local processing (FFmpeg, Whisper, OCR) and remote generation run in separate
lanes, so a long transcription never blocks sending an already prepared lecture
and vice versa. Inside a lane tasks run one after another.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..job_runner import CancellationToken, JobCancelledError

ProgressCallback = Callable[[int, str], None]
TaskHandler = Callable[["Task", ProgressCallback, CancellationToken], "str | None"]

ACTIVE_STATUSES = frozenset({"queued", "running"})
FINISHED_STATUSES = frozenset({"done", "failed", "cancelled"})
_MAX_FINISHED = 30


@dataclass
class Task:
    task_id: str
    lecture_key: str
    kind: str
    lane: str
    title: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    percent: int = 0
    message: str = "В очереди"
    error: str = ""
    result: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    token: CancellationToken = field(default_factory=CancellationToken, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "lecture_key": self.lecture_key,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "percent": self.percent,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "cancelling": self.status == "running" and self.token.is_cancelled,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class TaskQueue:
    """Run registered handlers on per-lane worker threads."""

    def __init__(
        self,
        handlers: dict[str, TaskHandler],
        lanes: dict[str, str],
        *,
        error_formatter: Callable[[Task, BaseException], str] | None = None,
        on_finished: Callable[[Task], None] | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._lanes = dict(lanes)
        self._error_formatter = error_formatter or (
            lambda _task, exc: str(exc) or type(exc).__name__
        )
        self._on_finished = on_finished
        self._condition = threading.Condition()
        self._tasks: dict[str, Task] = {}
        self._pending: dict[str, deque[str]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._ids = itertools.count(1)
        self._version = 0
        # Bumped only when lecture files may have changed (a task finished, an import…).
        self._library_version = 0
        self._closed = False

    # ------------------------------------------------------------------ public
    @property
    def version(self) -> int:
        with self._condition:
            return self._version

    @property
    def library_version(self) -> int:
        with self._condition:
            return self._library_version

    def submit(
        self,
        kind: str,
        lecture_key: str,
        title: str,
        params: dict[str, Any] | None = None,
    ) -> Task:
        if kind not in self._handlers:
            raise ValueError(f"Unknown task kind: {kind}")
        lane = self._lanes.get(kind, kind)
        with self._condition:
            if self._closed:
                raise RuntimeError("Очередь задач уже остановлена.")
            for existing in self._tasks.values():
                if (
                    existing.active
                    and existing.kind == kind
                    and existing.lecture_key == lecture_key
                ):
                    if params:
                        existing.params.update(params)
                        self._touch()
                    return existing
            task = Task(
                task_id=f"t{next(self._ids)}",
                lecture_key=lecture_key,
                kind=kind,
                lane=lane,
                title=title,
                params=dict(params or {}),
            )
            self._tasks[task.task_id] = task
            self._pending.setdefault(lane, deque()).append(task.task_id)
            self._ensure_worker(lane)
            self._library_version += 1
            self._touch()
            return task

    def cancel(self, task_id: str) -> bool:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None or not task.active:
                return False
            if task.status == "queued":
                self._finish(task, "cancelled", "Отменено")
                pending = self._pending.get(task.lane)
                if pending and task_id in pending:
                    pending.remove(task_id)
            else:
                task.message = "Останавливаем…"
                self._touch()
        task.token.cancel()
        return True

    def cancel_for_lecture(self, lecture_key: str) -> None:
        with self._condition:
            ids = [
                task.task_id
                for task in self._tasks.values()
                if task.lecture_key == lecture_key and task.active
            ]
        for task_id in ids:
            self.cancel(task_id)

    def dismiss(self, task_id: str) -> bool:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None or task.active:
                return False
            del self._tasks[task_id]
            self._touch()
            return True

    def get(self, task_id: str) -> Task | None:
        with self._condition:
            return self._tasks.get(task_id)

    def active_for(self, lecture_key: str) -> Task | None:
        with self._condition:
            running = [
                task
                for task in self._tasks.values()
                if task.lecture_key == lecture_key and task.active
            ]
            running.sort(key=lambda task: (task.status != "running", task.created_at))
            return running[0] if running else None

    def has_active(self) -> bool:
        with self._condition:
            return any(task.active for task in self._tasks.values())

    def snapshot(self) -> list[dict[str, Any]]:
        with self._condition:
            tasks = sorted(self._tasks.values(), key=lambda task: task.created_at)
            return [task.to_dict() for task in tasks]

    def wait_for_change(self, version: int, timeout: float) -> int:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._version == version and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._version

    def notify_external_change(self) -> None:
        """Let pollers know that library data changed outside of a task."""

        with self._condition:
            self._library_version += 1
            self._touch()

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._condition:
            self._closed = True
            tasks = [task for task in self._tasks.values() if task.active]
            self._condition.notify_all()
        for task in tasks:
            task.token.cancel()
        deadline = time.monotonic() + timeout
        for worker in list(self._workers.values()):
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

    # ----------------------------------------------------------------- workers
    def _touch(self) -> None:
        self._version += 1
        self._condition.notify_all()

    def _ensure_worker(self, lane: str) -> None:
        worker = self._workers.get(lane)
        if worker is not None and worker.is_alive():
            return
        worker = threading.Thread(
            target=self._work,
            args=(lane,),
            name=f"konspekt-{lane}",
            daemon=True,
        )
        self._workers[lane] = worker
        worker.start()

    def _work(self, lane: str) -> None:
        while True:
            with self._condition:
                pending = self._pending.get(lane)
                if self._closed or not pending:
                    self._workers.pop(lane, None)
                    return
                task = self._tasks.get(pending.popleft())
                if task is None or task.status != "queued":
                    continue
                task.status = "running"
                task.started_at = time.time()
                task.message = "Начинаем…"
                self._touch()
            self._run(task)

    def _run(self, task: Task) -> None:
        handler = self._handlers[task.kind]

        def progress(percent: int, message: str) -> None:
            with self._condition:
                if task.status != "running":
                    return
                task.percent = max(task.percent, min(100, max(0, int(percent))))
                if message and not task.token.is_cancelled:
                    task.message = str(message)
                self._touch()

        try:
            task.token.check_cancelled()
            result = handler(task, progress, task.token)
        except JobCancelledError:
            outcome = ("cancelled", "Отменено", "")
        except Exception as exc:
            if task.token.is_cancelled:
                outcome = ("cancelled", "Отменено", "")
            else:
                outcome = ("failed", "Ошибка", self._format_error(task, exc))
        else:
            if task.token.is_cancelled:
                outcome = ("cancelled", "Отменено", "")
            else:
                outcome = ("done", "Готово", "")
                task.result = result or ""
        with self._condition:
            self._finish(task, *outcome)
        if self._on_finished is not None:
            try:
                self._on_finished(task)
            except Exception:
                pass

    def _format_error(self, task: Task, exc: BaseException) -> str:
        try:
            return self._error_formatter(task, exc)
        except Exception:
            return str(exc) or type(exc).__name__

    def _finish(self, task: Task, status: str, message: str, error: str = "") -> None:
        task.status = status
        task.message = message
        task.error = error
        if status == "done":
            task.percent = 100
        task.finished_at = time.time()
        finished = [item for item in self._tasks.values() if not item.active]
        if len(finished) > _MAX_FINISHED:
            finished.sort(key=lambda item: item.finished_at or 0)
            for stale in finished[: len(finished) - _MAX_FINISHED]:
                self._tasks.pop(stale.task_id, None)
        self._library_version += 1
        self._touch()
