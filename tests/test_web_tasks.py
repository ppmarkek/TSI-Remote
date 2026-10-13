from __future__ import annotations

import threading
import time
import unittest

from konspekt.job_runner import JobCancelledError
from konspekt.web.tasks import TaskQueue


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []
        self.finished: list[str] = []

        def slow(task, progress, token):
            self.started.append(task.task_id)
            progress(40, "Работаем")
            while not self.release.wait(0.01):
                token.check_cancelled()
            return f"готово {task.lecture_key}"

        def quick(task, progress, token):
            self.started.append(task.task_id)
            return "ok"

        def broken(task, progress, token):
            raise RuntimeError("всё сломалось")

        self.queue = TaskQueue(
            {"slow": slow, "quick": quick, "broken": broken},
            {"slow": "local", "quick": "remote", "broken": "remote"},
            on_finished=lambda task: self.finished.append(task.task_id),
        )
        self.addCleanup(self.queue.shutdown, 1.0)
        self.addCleanup(self.release.set)

    def test_runs_task_and_reports_progress(self) -> None:
        task = self.queue.submit("slow", "lec", "Лекция")
        self.assertTrue(wait_until(lambda: self.queue.get(task.task_id).percent == 40))
        self.assertEqual(self.queue.get(task.task_id).message, "Работаем")
        self.assertIs(self.queue.active_for("lec"), task)

        self.release.set()

        self.assertTrue(wait_until(lambda: task.status == "done"))
        self.assertEqual((task.percent, task.result), (100, "готово lec"))
        self.assertTrue(wait_until(lambda: self.finished == [task.task_id]))
        self.assertFalse(self.queue.has_active())

    def test_duplicate_submission_returns_existing_task_and_merges_params(self) -> None:
        first = self.queue.submit("slow", "lec", "Лекция", {"a": 1})
        second = self.queue.submit("slow", "lec", "Лекция", {"b": 2})

        self.assertIs(first, second)
        self.assertEqual(first.params, {"a": 1, "b": 2})
        self.assertEqual(len(self.queue.snapshot()), 1)

    def test_lanes_run_independently(self) -> None:
        slow = self.queue.submit("slow", "one", "Первая")
        quick = self.queue.submit("quick", "two", "Вторая")

        self.assertTrue(wait_until(lambda: quick.status == "done"))
        self.assertEqual(slow.status, "running")

    def test_tasks_in_one_lane_run_in_order_and_queued_ones_cancel_at_once(self) -> None:
        first = self.queue.submit("slow", "one", "Первая")
        second = self.queue.submit("slow", "two", "Вторая")
        self.assertTrue(wait_until(lambda: first.status == "running"))
        self.assertEqual(second.status, "queued")

        self.assertTrue(self.queue.cancel(second.task_id))
        self.assertEqual(second.status, "cancelled")

        self.release.set()
        self.assertTrue(wait_until(lambda: first.status == "done"))
        self.assertEqual(self.started, [first.task_id])

    def test_cancelling_running_task(self) -> None:
        task = self.queue.submit("slow", "lec", "Лекция")
        self.assertTrue(wait_until(lambda: task.status == "running"))

        self.assertTrue(self.queue.cancel(task.task_id))
        self.assertEqual(task.to_dict()["cancelling"], task.status == "running")

        self.assertTrue(wait_until(lambda: task.status == "cancelled"))
        self.assertFalse(self.queue.cancel(task.task_id))

    def test_failure_is_reported_and_can_be_dismissed(self) -> None:
        task = self.queue.submit("broken", "lec", "Лекция")

        self.assertTrue(wait_until(lambda: task.status == "failed"))
        self.assertEqual(task.error, "всё сломалось")
        self.assertTrue(self.queue.dismiss(task.task_id))
        self.assertIsNone(self.queue.get(task.task_id))

    def test_versions_change_and_wait_wakes_up(self) -> None:
        version = self.queue.version
        library_version = self.queue.library_version

        threading.Timer(0.05, self.queue.notify_external_change).start()
        changed = self.queue.wait_for_change(version, timeout=2)

        self.assertGreater(changed, version)
        self.assertGreater(self.queue.library_version, library_version)
        self.assertEqual(self.queue.wait_for_change(changed, timeout=0.05), changed)

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.submit("nope", "lec", "Лекция")

    def test_handler_cancellation_error_marks_task_cancelled(self) -> None:
        def cancels(task, progress, token):
            raise JobCancelledError("stop")

        queue = TaskQueue({"x": cancels}, {})
        self.addCleanup(queue.shutdown, 1.0)
        task = queue.submit("x", "lec", "Лекция")

        self.assertTrue(wait_until(lambda: task.status == "cancelled"))


if __name__ == "__main__":
    unittest.main()
