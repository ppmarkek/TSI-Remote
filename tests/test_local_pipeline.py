from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.bbb_import import BBBRecording
from konspekt.lecture_manifest import ALGORITHM_VERSION, LectureManifest, file_sha256
from konspekt.library_manager import save_to_library
from konspekt.local_pipeline import (
    LocalProcessingError,
    TranscriptSegment,
    default_lecture_directory,
    faster_whisper_transcribe,
    prepare_lecture,
)


def _save_transcription_manifest(target: Path, recording: BBBRecording) -> None:
    manifest = LectureManifest.for_recording(
        recording.title, recording.meeting_id, recording.source_url, target
    )
    audio_wav = target / "audio.wav"
    tr_md = target / "transcript.md"
    tr_json = target / "transcript.json"
    manifest.record_stage_success(
        "transcription",
        fingerprint={
            "whisper_model": "base",
            "language": "auto",
            "algorithm_version": ALGORITHM_VERSION,
            "input_audio_sha256": file_sha256(audio_wav) if audio_wav.is_file() else "",
        },
        outputs={
            "transcript.md": file_sha256(tr_md) if tr_md.is_file() else "",
            "transcript.json": file_sha256(tr_json) if tr_json.is_file() else "",
        },
    )
    manifest.save(target / "lecture-manifest.json")


class LocalPipelineTests(unittest.TestCase):
    def test_defensively_contains_untrusted_library_meeting_id(self) -> None:
        recording = BBBRecording(
            meeting_id="../../outside",
            source_url="https://example.test/playback?meetingId=unsafe",
            title="Unsafe identifier",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                directory = default_lecture_directory(recording)
            base = Path(temporary) / "Konspekt" / "lectures"
            directory.relative_to(base)
            self.assertTrue(directory.name.startswith("lecture-"))

    def test_separates_storage_for_same_id_on_different_hosts(self) -> None:
        first = BBBRecording(
            meeting_id="shared-meeting",
            source_url="https://bbb.first.test/playback?meetingId=shared-meeting",
            title="First host",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://bbb.first.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        second = BBBRecording(
            meeting_id=first.meeting_id,
            source_url="https://bbb.second.test/playback?meetingId=shared-meeting",
            title="Second host",
            imported_at="2026-07-15T11:00:00+00:00",
            audio_video_url="https://bbb.second.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"LOCALAPPDATA": temporary}):
                save_to_library(first)
                save_to_library(second)
                first_directory = default_lecture_directory(first)
                second_directory = default_lecture_directory(second)

                reimported_first = BBBRecording(
                    meeting_id=first.meeting_id,
                    source_url=first.source_url,
                    title="First host, reimported",
                    imported_at="2026-07-15T12:00:00+00:00",
                    audio_video_url=first.audio_video_url,
                    screen_video_url=None,
                    slides=(),
                )
                save_to_library(reimported_first)
                reimported_first_directory = default_lecture_directory(reimported_first)
                stable_second_directory = default_lecture_directory(second)

        self.assertNotEqual(first_directory, second_directory)
        self.assertTrue(first_directory.name.startswith("shared-meeting-"))
        self.assertTrue(second_directory.name.startswith("shared-meeting-"))
        self.assertEqual(reimported_first_directory, first_directory)
        self.assertEqual(stable_second_directory, second_directory)

    def test_default_ocr_reader_raises_when_both_tesseract_attempts_fail(self) -> None:
        failed = subprocess.CompletedProcess(
            ["tesseract"],
            1,
            "",
            "language data unavailable",
        )

        with (
            patch("konspekt.local_pipeline._find_tesseract_executable", return_value="tesseract"),
            patch("konspekt.local_pipeline.subprocess.run", return_value=failed) as run,
        ):
            from konspekt.local_pipeline import default_ocr_reader

            reader = default_ocr_reader()
            self.assertIsNotNone(reader)
            with self.assertRaisesRegex(LocalProcessingError, "Tesseract"):
                reader(Path("frame-0001.jpg"))  # type: ignore[misc]

        self.assertEqual(run.call_count, 2)

    def test_normalises_whisper_model_initialisation_failure(self) -> None:
        def failing_model_factory(*_: object, **__: object) -> object:
            raise RuntimeError("model cache is unavailable")

        with self.assertRaises(LocalProcessingError) as raised:
            faster_whisper_transcribe("small", model_factory=failing_model_factory)

        self.assertIn("Whisper", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_prepares_transcript_frames_and_local_ocr(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-1",
            source_url="https://example.test/playback?meetingId=meeting-1",
            title="Test lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(),
        )

        def downloader(_: str, destination: Path, __: object) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"media")

        def transcribe(_: Path, __: str | None) -> tuple[TranscriptSegment, ...]:
            return (TranscriptSegment(1.2, 4.8, "Hello local transcription"),)

        progress_updates: list[tuple[int, str]] = []

        def run_ffmpeg(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = Path(command[-1])
            if "frame-%04d.jpg" in str(output):
                output.parent.mkdir(parents=True, exist_ok=True)
                (output.parent / "frame-0001.jpg").write_bytes(b"frame")
                (output.parent / "frame-0002.jpg").write_bytes(b"frame")
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"audio")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                prepared = prepare_lecture(
                    recording,
                    directory=Path(temporary),
                    downloader=downloader,
                    transcriber=transcribe,
                    ocr_reader=lambda frame: f"Text from {frame.name}",
                    command_runner=run_ffmpeg,
                    progress=lambda percent, message: progress_updates.append((percent, message)),
                )

            transcript = prepared.transcript_path.read_text(encoding="utf-8")
            notes = (Path(temporary) / "screen-notes.json").read_text(encoding="utf-8")

        self.assertIn("00:00:01", transcript)
        self.assertIn("Hello local transcription", transcript)
        self.assertIn("frame-0002.jpg", notes)
        self.assertEqual(prepared.frame_count, 2)
        self.assertEqual(progress_updates[-1][0], 100)
        self.assertEqual(
            [percent for percent, _ in progress_updates],
            sorted(percent for percent, _ in progress_updates),
        )

    def test_disabling_screen_processing_skips_deskshare_download(self) -> None:
        recording = BBBRecording(
            meeting_id="audio-only-processing",
            source_url="https://example.test/playback?meetingId=audio-only-processing",
            title="Fast lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(),
        )
        downloaded_urls: list[str] = []

        def downloader(url: str, destination: Path, _: object) -> None:
            downloaded_urls.append(url)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"media")

        def run_ffmpeg(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output = Path(command[-1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"audio")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                prepared = prepare_lecture(
                    recording,
                    directory=Path(temporary),
                    downloader=downloader,
                    transcriber=lambda *_: (TranscriptSegment(0, 1, "Text"),),
                    command_runner=run_ffmpeg,
                    enable_ocr=False,
                )

        self.assertEqual(downloaded_urls, [recording.audio_video_url])
        self.assertEqual(prepared.frame_count, 0)
        self.assertIsNone(prepared.screen_notes_path)

    def test_reuses_existing_audio_and_skips_screen_when_absent(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-2",
            source_url="https://example.test/playback?meetingId=meeting-2",
            title="Audio lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")

            def run_ffmpeg(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"audio")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                prepared = prepare_lecture(
                    recording,
                    directory=target,
                    downloader=lambda *_: self.fail("download should not run"),
                    transcriber=lambda *_: (),
                    command_runner=run_ffmpeg,
                )

        self.assertIsNone(prepared.screen_notes_path)
        self.assertEqual(prepared.frame_count, 0)

    def test_resumes_from_existing_transcript_without_transcribing_again(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-resume",
            source_url="https://example.test/playback?meetingId=meeting-resume",
            title="Resume lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        progress_updates: list[tuple[int, str]] = []

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")
            (target / "audio.wav").write_bytes(b"audio")
            (target / "transcript.json").write_text("[]", encoding="utf-8")
            (target / "transcript.md").write_text(
                "# Transcript\n\nAlready prepared.\n",
                encoding="utf-8",
            )
            _save_transcription_manifest(target, recording)

            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                prepared = prepare_lecture(
                    recording,
                    directory=target,
                    downloader=lambda *_: self.fail("download should not run"),
                    transcriber=lambda *_: self.fail("transcriber should not run"),
                    command_runner=lambda *_args, **_kwargs: self.fail("FFmpeg should not run"),
                    progress=lambda percent, message: progress_updates.append((percent, message)),
                )

            transcript = prepared.transcript_path.read_text(encoding="utf-8")

        self.assertIn("Already prepared", transcript)
        self.assertEqual(progress_updates[-1][0], 100)

    def test_one_ocr_frame_failure_does_not_abort_the_pipeline(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-ocr-resume",
            source_url="https://example.test/playback?meetingId=meeting-ocr-resume",
            title="OCR resume lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(),
        )
        progress_updates: list[tuple[int, str]] = []

        def read_frame(frame: Path) -> str:
            if frame.name == "frame-0001.jpg":
                raise UnicodeDecodeError(
                    "cp1251",
                    b"\x98",
                    0,
                    1,
                    "character maps to <undefined>",
                )
            return "Readable text"

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")
            (target / "audio.wav").write_bytes(b"audio")
            (target / "transcript.json").write_text("[]", encoding="utf-8")
            (target / "transcript.md").write_text("# Transcript\n", encoding="utf-8")
            _save_transcription_manifest(target, recording)
            (target / "deskshare.webm").write_bytes(b"screen")
            frames = target / "frames"
            frames.mkdir()
            (frames / "frame-0001.jpg").write_bytes(b"frame one")
            (frames / "frame-0002.jpg").write_bytes(b"frame two")

            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                prepared = prepare_lecture(
                    recording,
                    directory=target,
                    downloader=lambda *_: self.fail("download should not run"),
                    transcriber=lambda *_: self.fail("transcriber should not run"),
                    ocr_reader=read_frame,
                    command_runner=lambda *_args, **_kwargs: self.fail("FFmpeg should not run"),
                    progress=lambda percent, message: progress_updates.append((percent, message)),
                )

            self.assertIsNotNone(prepared.screen_notes_path)
            notes = prepared.screen_notes_path.read_text(encoding="utf-8")

        self.assertIn("frame-0002.jpg", notes)
        self.assertIn("Readable text", notes)
        self.assertNotIn("frame-0001.jpg", notes)
        self.assertEqual(progress_updates[-1][0], 100)
        self.assertEqual(
            [percent for percent, _ in progress_updates],
            sorted(percent for percent, _ in progress_updates),
        )

    def test_failed_audio_extraction_does_not_cache_partial_output(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-partial-audio",
            source_url="https://example.test/playback?meetingId=meeting-partial-audio",
            title="Partial audio",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")

            def failing_ffmpeg(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(command, 1, "", "failed")

            with (
                patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"),
                self.assertRaisesRegex(Exception, "FFmpeg"),
            ):
                prepare_lecture(
                    recording,
                    directory=target,
                    transcriber=lambda *_: (),
                    command_runner=failing_ffmpeg,
                )

            self.assertFalse((target / "audio.wav").exists())
            self.assertFalse((target / "audio.part.wav").exists())

    def test_all_ocr_failures_are_retryable_and_not_cached(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-all-ocr-fail",
            source_url="https://example.test/playback?meetingId=meeting-all-ocr-fail",
            title="OCR failure",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")
            (target / "audio.wav").write_bytes(b"audio")
            (target / "transcript.json").write_text("[]", encoding="utf-8")
            (target / "transcript.md").write_text("# Transcript\n", encoding="utf-8")
            _save_transcription_manifest(target, recording)
            (target / "deskshare.webm").write_bytes(b"screen")
            frames = target / "frames"
            frames.mkdir()
            (frames / "frame-0001.jpg").write_bytes(b"frame")

            def broken_ocr(_: Path) -> str | None:
                raise OSError("tesseract failed")

            with (
                patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"),
                self.assertRaisesRegex(Exception, "Tesseract"),
            ):
                prepare_lecture(
                    recording,
                    directory=target,
                    ocr_reader=broken_ocr,
                )

            self.assertFalse((target / "screen-notes.json").exists())

    def test_frame_interval_change_invalidates_stale_frames(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-interval-change",
            source_url="https://example.test/playback?meetingId=meeting-interval-change",
            title="Interval lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url="https://example.test/deskshare/deskshare.webm",
            slides=(),
        )

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "webcams.webm").write_bytes(b"media")
            (target / "audio.wav").write_bytes(b"audio")
            (target / "transcript.json").write_text("[]", encoding="utf-8")
            (target / "transcript.md").write_text("# Transcript\n", encoding="utf-8")
            _save_transcription_manifest(target, recording)
            (target / "deskshare.webm").write_bytes(b"screen")

            extracted_intervals: list[int] = []

            def run_ffmpeg(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output = Path(command[-1])
                if "frame-%04d.jpg" in str(output):
                    for arg in command:
                        if arg.startswith("fps=1/"):
                            extracted_intervals.append(int(arg.split("/")[1]))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    (output.parent / "frame-0001.jpg").write_bytes(b"frame1")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("konspekt.local_pipeline.resolve_ffmpeg", return_value="ffmpeg"):
                # First run with interval 20
                prepare_lecture(
                    recording,
                    directory=target,
                    frame_interval_seconds=20,
                    command_runner=run_ffmpeg,
                    ocr_reader=lambda _: "Note 20",
                )
                self.assertEqual(extracted_intervals, [20])

                # Second run with interval 10 should invalidate old frames and re-run ffmpeg
                prepare_lecture(
                    recording,
                    directory=target,
                    frame_interval_seconds=10,
                    command_runner=run_ffmpeg,
                    ocr_reader=lambda _: "Note 10",
                )
                self.assertEqual(extracted_intervals, [20, 10])


if __name__ == "__main__":
    unittest.main()
