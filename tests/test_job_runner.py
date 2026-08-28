from __future__ import annotations

import time
import unittest

from konspekt.job_runner import (
    CancellationToken,
    JobEvent,
    JobEventType,
    JobRunner,
)


class JobRunnerTests(unittest.TestCase):
    def test_job_events_arrive_in_order(self) -> None:
        events: list[JobEvent] = []
        runner = JobRunner()

        def successful_job(token: CancellationToken, progress: callable) -> str:
            progress(25, "Quarter done")
            progress(75, "Almost done")
            return "All finished"

        runner.run_job(successful_job, on_event=events.append)
        time.sleep(0.1)

        event_types = [e.event_type for e in events]
        self.assertEqual(
            event_types,
            [
                JobEventType.STARTED,
                JobEventType.PROGRESS,
                JobEventType.PROGRESS,
                JobEventType.COMPLETED,
            ],
        )
        self.assertEqual(events[-1].result, "All finished")

    def test_job_cancellation(self) -> None:
        events: list[JobEvent] = []
        runner = JobRunner()

        def long_running_job(token: CancellationToken, progress: callable) -> None:
            progress(10, "Starting")
            for _ in range(20):
                token.check_cancelled()
                time.sleep(0.05)

        token = runner.run_job(long_running_job, on_event=events.append)
        time.sleep(0.08)
        token.cancel()
        time.sleep(0.1)

        event_types = [e.event_type for e in events]
        self.assertIn(JobEventType.CANCELLED, event_types)

    def test_job_failure_propagates_cleanly(self) -> None:
        events: list[JobEvent] = []
        runner = JobRunner()

        def failing_job(token: CancellationToken, progress: callable) -> None:
            raise RuntimeError("Disk full simulation")

        runner.run_job(failing_job, on_event=events.append)
        time.sleep(0.05)

        self.assertEqual(events[-1].event_type, JobEventType.FAILED)
        self.assertIn("Disk full simulation", events[-1].error or "")


if __name__ == "__main__":
    unittest.main()
