from __future__ import annotations

import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import konspekt
from konspekt.bbb_import import BBBImportError, BBBRecording
from konspekt.deepseek_handoff import (
    DeepSeekHandoffError,
    launch_deepseek_handoff,
    prepare_deepseek_handoff,
)
from konspekt.job_runner import terminate_process_tree
from konspekt.lecture_manifest import LectureManifest, ManifestError
from konspekt.lesson_export import _render_pure_python_pdf
from konspekt.library_manager import (
    export_lecture_archive,
    list_trash,
    move_to_trash,
    restore_from_trash,
    save_library,
)
from konspekt.local_media_import import import_local_media_file
from konspekt.platform_services import PlatformAppPaths, SecretStoreError
from konspekt.settings import load_settings


class _LockedSecretStore:
    def get_secret(self, service: str, account: str) -> str | None:
        raise SecretStoreError("locked")

    def set_secret(self, service: str, account: str, secret: str) -> None:
        raise SecretStoreError("locked")

    def delete_secret(self, service: str, account: str) -> None:
        raise SecretStoreError("locked")


class ReviewRegressionTests(unittest.TestCase):
    def test_python_310_datetime_compatibility_is_installed_by_package(self) -> None:
        self.assertTrue(konspekt.__version__)
        self.assertIs(datetime.UTC, datetime.timezone.utc)

    def test_platform_override_controls_all_standard_paths(self) -> None:
        mac = PlatformAppPaths(system_override="darwin")
        self.assertIn("Application Support", str(mac.data_dir))
        self.assertIn("Caches", str(mac.cache_dir))
        self.assertIn("Logs", str(mac.log_dir))

    def test_locked_keyring_does_not_delete_legacy_secret(self) -> None:
        payload = {
            "schema_version": 1,
            "api_provider": "openai",
            "api_model": "gpt-5.6-luna",
            "api_key_protected": "legacy-protected-data",
            "chatgpt_model": "gpt-5.5",
            "whisper_model": "base",
            "frame_interval_seconds": 60,
            "ocr_enabled": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("konspekt.settings._unprotect_secret", return_value="decrypted-key"):
                loaded = load_settings(path=path, secret_store=_LockedSecretStore())
            rewritten = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded.api_key, "decrypted-key")
        self.assertEqual(rewritten["api_key_protected"], "legacy-protected-data")

    def test_deepseek_final_check_retains_exact_meeting_id(self) -> None:
        recording = self._recording("meeting-deepseek")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            context = directory / "lesson-context.md"
            context.write_text("Clean context", encoding="utf-8")
            (directory / "lesson-prompt.md").write_text("Clean prompt", encoding="utf-8")
            handoff = prepare_deepseek_handoff(recording, directory=directory)
            context.write_text(f"Altered {recording.meeting_id}", encoding="utf-8")
            opened: list[str] = []
            with self.assertRaises(DeepSeekHandoffError):
                launch_deepseek_handoff(
                    handoff,
                    open_url=lambda url: opened.append(url) or True,
                )
            self.assertEqual(opened, [])

    def test_local_import_repairs_corrupted_cache_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lecture.mp3"
            source_bytes = b"complete media" * 100
            source.write_bytes(source_bytes)
            library = root / "library.json"
            _, lecture_dir = import_local_media_file(source, library_path=library)
            destination = lecture_dir / "audio.mp4"
            destination.write_bytes(b"partial")
            import_local_media_file(source, library_path=library)
            self.assertEqual(destination.read_bytes(), source_bytes)
            self.assertFalse((lecture_dir / "audio.mp4.part").exists())

    def test_process_tree_termination_kills_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_file = Path(temporary) / "child.pid"
            script = (
                "import pathlib, subprocess, sys, time; "
                "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
            )
            parent = subprocess.Popen(
                [sys.executable, "-c", script, str(child_pid_file)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = 0
            try:
                self._wait_for(child_pid_file.is_file, timeout=5)
                child_pid = int(child_pid_file.read_text())
                terminate_process_tree(parent, grace_period_seconds=1)
                self._wait_for(lambda: parent.poll() is not None, timeout=5)
                self._wait_for(lambda: not self._process_is_running(child_pid), timeout=5)
            finally:
                if parent.poll() is None:
                    parent.kill()
                if child_pid and self._process_is_running(child_pid):
                    self._force_kill(child_pid)

    def test_invalid_manifest_schema_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lecture-manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "bad",
                        "lecture_id": "lecture",
                        "meeting_id": "meeting",
                        "source_url": "https://bbb.test",
                        "stages": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ManifestError):
                LectureManifest.load(path)
            fresh = LectureManifest.for_recording(
                "Title", "meeting", "https://bbb.test", path.parent
            )
            self.assertEqual(fresh.meeting_id, "meeting")

    def test_pdf_fallback_embeds_rendered_cyrillic_pages(self) -> None:
        payload = _render_pure_python_pdf(
            "Лекция по алгоритмам",
            "# Введение\nКириллица должна отображаться без подстановки шрифта.",
        )
        self.assertIn(b"/Subtype /Image", payload)
        self.assertNotIn(b"/Identity-H", payload)
        self.assertGreater(len(payload), 5_000)

    def test_archive_does_not_follow_external_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slides = root / "lecture" / "slides"
            slides.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = slides / "outside.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            destination = root / "lecture.zip"
            export_lecture_archive(slides.parent, destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertNotIn("slides/outside.txt", archive.namelist())

    def test_trash_rollback_and_restore_collision_are_non_destructive(self) -> None:
        recording = self._recording("m-rollback")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library.json"
            save_library([recording], path=library)
            lecture = root / recording.meeting_id
            lecture.mkdir()
            (lecture / "lesson.md").write_text("safe", encoding="utf-8")
            original_library = library.read_text(encoding="utf-8")

            with patch(
                "konspekt.library_manager.save_library",
                side_effect=BBBImportError("simulated failure"),
            ):
                with self.assertRaises(BBBImportError):
                    move_to_trash(library, recording.meeting_id, root)
            self.assertTrue((lecture / "lesson.md").is_file())
            self.assertEqual(library.read_text(encoding="utf-8"), original_library)
            self.assertEqual(list_trash(root), [])

            move_to_trash(library, recording.meeting_id, root)
            lecture.mkdir()
            (lecture / "keep.txt").write_text("do not delete", encoding="utf-8")
            with self.assertRaises(ValueError):
                restore_from_trash(library, recording.meeting_id, root)
            self.assertEqual((lecture / "keep.txt").read_text(), "do not delete")

    def test_build_script_fails_when_expected_windows_artifact_is_missing(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "build_package.py"
        spec = importlib.util.spec_from_file_location("build_package_regression", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packaging = root / "packaging"
            packaging.mkdir()
            (packaging / "Konspekt.windows.spec").write_text("# spec", encoding="utf-8")
            with (
                patch.object(module, "PROJECT_ROOT", root),
                patch.object(module.sys, "platform", "win32"),
                patch.object(module.subprocess, "run") as run,
            ):
                run.return_value.returncode = 0
                self.assertEqual(module.main(), 1)

    @staticmethod
    def _recording(meeting_id: str) -> BBBRecording:
        return BBBRecording(
            meeting_id=meeting_id,
            source_url=f"https://bbb.test/playback?meetingId={meeting_id}",
            title="Lecture",
            imported_at="2026-08-01T10:00:00+00:00",
            audio_video_url="https://bbb.test/video.mp4",
            screen_video_url=None,
            slides=(),
        )

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0 and f'"{pid}"' in result.stdout
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        state = result.stdout.strip()
        return result.returncode == 0 and bool(state) and not state.startswith("Z")

    @staticmethod
    def _force_kill(pid: int) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.kill(pid, 9)
        except OSError:
            pass

    @staticmethod
    def _wait_for(predicate, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError("condition was not met before timeout")


if __name__ == "__main__":
    unittest.main()
