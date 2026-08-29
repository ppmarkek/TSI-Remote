from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from konspekt.download_manager import DownloadError, download_file_resumable
from konspekt.job_runner import CancellationToken, JobCancelledError


class FakeStreamResponse:
    def __init__(
        self, chunks: list[bytes], status_code: int = 200, content_length: int | None = None
    ) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}

    def iter_content(self, chunk_size: int = 65536) -> list[bytes]:
        return list(self.chunks)

    def close(self) -> None:
        pass


class FakeClient:
    def __init__(self, response: FakeStreamResponse) -> None:
        self.response = response
        self.last_headers: dict[str, str] = {}

    def get(
        self, url: str, headers: dict[str, str] | None = None, **kwargs: object
    ) -> FakeStreamResponse:
        self.last_headers = headers or {}
        return self.response


class DownloadResumeTests(unittest.TestCase):
    def test_clean_download_writes_and_replaces(self) -> None:
        response = FakeStreamResponse([b"chunk1", b"chunk2"], status_code=200, content_length=12)
        client = FakeClient(response)

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "audio.mp4"
            download_file_resumable("https://bbb.test/audio.mp4", target, session=client)

            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), b"chunk1chunk2")
            self.assertFalse(target.with_name("audio.mp4.part").exists())

    def test_resumes_when_server_accepts_range_206(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "video.mp4"
            part = target.with_name("video.mp4.part")
            part.write_bytes(b"initial_data_")

            response = FakeStreamResponse([b"resumed_data"], status_code=206, content_length=12)
            client = FakeClient(response)

            download_file_resumable("https://bbb.test/video.mp4", target, session=client)

            self.assertEqual(client.last_headers.get("Range"), "bytes=13-")
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), b"initial_data_resumed_data")

    def test_overwrites_part_when_server_returns_200_without_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "video.mp4"
            part = target.with_name("video.mp4.part")
            part.write_bytes(b"stale_partial_")

            response = FakeStreamResponse(
                [b"fresh_entire_data"], status_code=200, content_length=17
            )
            client = FakeClient(response)

            download_file_resumable("https://bbb.test/video.mp4", target, session=client)

            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), b"fresh_entire_data")

    def test_cancellation_leaves_part_intact_for_later_resume(self) -> None:
        token = CancellationToken()
        response = FakeStreamResponse(
            [b"part1", b"part2", b"part3"], status_code=200, content_length=15
        )
        client = FakeClient(response)

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "audio.mp4"
            token.cancel()

            with self.assertRaises(JobCancelledError):
                download_file_resumable(
                    "https://bbb.test/audio.mp4", target, token=token, session=client
                )

            self.assertFalse(target.exists())

    def test_insufficient_disk_space_raises_download_error(self) -> None:
        response = FakeStreamResponse(
            [b"data"], status_code=200, content_length=100 * 1024 * 1024 * 1024
        )
        client = FakeClient(response)

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "huge.mp4"
            fake_usage = MagicMock(free=1024)  # 1 KB free
            with patch("shutil.disk_usage", return_value=fake_usage):
                with self.assertRaises(DownloadError) as exc_info:
                    download_file_resumable("https://bbb.test/huge.mp4", target, session=client)

                self.assertIn("Недостаточно свободного места", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
