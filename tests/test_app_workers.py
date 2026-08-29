from __future__ import annotations

import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from konspekt.app import StudyApp
from konspekt.bbb_import import BBBRecording
from konspekt.chatgpt_account import (
    ChatGPTAccountError,
    ChatGPTAccountStatus,
    ChatGPTGenerationResult,
    ChatGPTModel,
)
from konspekt.local_pipeline import LocalProcessingError
from konspekt.settings import AppSettings


class DeferredApp:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []
        self.errors: list[str] = []

    def after(self, delay_ms: int, callback: Callable[[], None]) -> None:
        if delay_ms != 0:
            raise AssertionError(f"unexpected delay: {delay_ms}")
        self.callbacks.append(callback)

    def _finish_processing_error(self, message: str) -> None:
        self.errors.append(message)

    def _finish_processing_success(self, _: object) -> None:
        raise AssertionError("failed processing must not report success")


class BusyApp:
    _processing_active = True

    def _prepare_processing_state(self, *_: object, **__: object) -> None:
        raise AssertionError("a second processing operation must not start")

    def show_processing_screen(self, *_: object, **__: object) -> None:
        raise AssertionError("a second processing screen must not replace the active one")


class FakeVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class FakeCombobox:
    def __init__(self) -> None:
        self.values: tuple[str, ...] = ()

    def winfo_exists(self) -> bool:
        return True

    def configure(self, **kwargs: object) -> None:
        values = kwargs.get("values")
        if isinstance(values, tuple):
            self.values = values


class DeferredChatGPTApp:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []
        self._processing_operation_id = 7
        self._chatgpt_account_operation_id = 4
        self.progress: list[tuple[int, str]] = []
        self.errors: list[str] = []
        self.account_results: list[tuple[ChatGPTAccountStatus, list[ChatGPTModel], str]] = []
        self.generation_results: list[tuple[BBBRecording, ChatGPTGenerationResult]] = []

    def after(self, delay_ms: int, callback: Callable[[], None]) -> None:
        if delay_ms != 0:
            raise AssertionError(f"unexpected delay: {delay_ms}")
        self.callbacks.append(callback)

    def _set_processing_progress(self, percent: int, message: str) -> None:
        self.progress.append((percent, message))

    def _finish_processing_error(self, message: str) -> None:
        self.errors.append(message)

    def _finish_chatgpt_account_refresh(
        self,
        _: int,
        status: ChatGPTAccountStatus,
        models: list[ChatGPTModel],
        model_error: str,
    ) -> None:
        self.account_results.append((status, models, model_error))

    def _finish_chatgpt_generation_success(
        self,
        recording: BBBRecording,
        result: ChatGPTGenerationResult,
    ) -> None:
        self.generation_results.append((recording, result))


class StartChatGPTApp:
    def __init__(self) -> None:
        self._processing_active = False
        self.settings = AppSettings(chatgpt_model="gpt-5.5")
        self._settings_chatgpt_model = FakeVar("gpt-5.5")
        self._chatgpt_model_summary = FakeVar()
        self.screen_calls: list[BBBRecording] = []

    def _next_chatgpt_account_operation(self) -> int:
        return 3

    def _prepare_processing_state(self, *_: object, **__: object) -> int:
        return 8

    def show_processing_screen(self, recording: BBBRecording, **_: object) -> None:
        self.screen_calls.append(recording)

    def _chatgpt_generation_worker(self, *_: object) -> None:
        raise AssertionError("the worker must not run inline")

    def _set_active_chatgpt_model(self, model: str) -> None:
        StudyApp._set_active_chatgpt_model(self, model)  # type: ignore[arg-type]


class AccountRefreshApp:
    def __init__(self) -> None:
        self._chatgpt_account_operation_id = 6
        self._chatgpt_login_active = True
        self._chatgpt_account: ChatGPTAccountStatus | None = None
        self.settings = AppSettings(chatgpt_model="gpt-5.5")
        self._settings_chatgpt_model = FakeVar("gpt-5.5")
        self._chatgpt_model_summary = FakeVar()
        self._chatgpt_generation_action = FakeVar()
        self._chatgpt_model_combobox = FakeCombobox()
        self.status = ""

    def _set_chatgpt_controls_busy(self, _: bool) -> None:
        pass

    def _set_chatgpt_status(self, message: str, _: str) -> None:
        self.status = message

    def _set_active_chatgpt_model(self, model: str) -> None:
        StudyApp._set_active_chatgpt_model(self, model)  # type: ignore[arg-type]

    def after(self, delay_ms: int, callback: Callable[[], None]) -> None:
        callback()

    def _finish_chatgpt_account_error(self, operation_id: int, message: str) -> None:
        StudyApp._finish_chatgpt_account_error(self, operation_id, message)  # type: ignore[arg-type]


class StartLoginApp:
    def __init__(self) -> None:
        self._chatgpt_login_active = False
        self.statuses: list[str] = []
        self.busy_states: list[bool] = []

    def _next_chatgpt_account_operation(self) -> int:
        return 5

    def _set_chatgpt_status(self, message: str, _: str) -> None:
        self.statuses.append(message)

    def _set_chatgpt_controls_busy(self, busy: bool) -> None:
        self.busy_states.append(busy)

    def _chatgpt_account_worker(self, *_: object) -> None:
        raise AssertionError("login must not run inline")


class AppWorkerTests(unittest.TestCase):
    def test_local_processing_delivers_domain_error_after_except_scope(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-worker-error",
            source_url="https://example.test/playback?meetingId=meeting-worker-error",
            title="Worker error lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        app = DeferredApp()

        with patch(
            "konspekt.app.prepare_lecture",
            side_effect=LocalProcessingError("Whisper could not start"),
        ):
            StudyApp._local_processing_worker(app, recording)  # type: ignore[arg-type]

        self.assertEqual(len(app.callbacks), 1)
        app.callbacks[0]()
        self.assertEqual(app.errors, ["Whisper could not start"])

    def test_stale_worker_callback_cannot_mutate_a_new_operation(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-worker-stale",
            source_url="https://example.test/playback?meetingId=meeting-worker-stale",
            title="Stale worker lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        app = DeferredApp()
        app._processing_operation_id = 2

        with patch(
            "konspekt.app.prepare_lecture",
            side_effect=LocalProcessingError("old job failed"),
        ):
            StudyApp._local_processing_worker(  # type: ignore[arg-type]
                app,
                recording,
                operation_id=1,
            )

        app.callbacks[0]()
        self.assertEqual(app.errors, [])

    def test_active_processing_blocks_a_second_start(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-worker-busy",
            source_url="https://example.test/playback?meetingId=meeting-worker-busy",
            title="Busy worker lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

        StudyApp.start_local_processing(BusyApp(), recording)  # type: ignore[arg-type]

    def test_chatgpt_generation_starts_in_a_background_thread(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-chatgpt-start",
            source_url="https://example.test/playback?meetingId=meeting-chatgpt-start",
            title="ChatGPT start lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        app = StartChatGPTApp()
        app._settings_chatgpt_model.set("gpt-5.4")

        with patch("konspekt.app.threading.Thread") as thread_class:
            StudyApp.start_chatgpt_generation(app, recording)  # type: ignore[arg-type]

        self.assertEqual(app.screen_calls, [recording])
        self.assertEqual(app.settings.chatgpt_model, "gpt-5.4")
        self.assertEqual(thread_class.call_args.kwargs["args"][1], "gpt-5.4")
        self.assertTrue(thread_class.call_args.kwargs["daemon"])
        thread_class.return_value.start.assert_called_once_with()

    def test_account_refresh_replaces_an_unavailable_selected_model(self) -> None:
        app = AccountRefreshApp()
        signed_in = ChatGPTAccountStatus(True, "student@example.test", "plus")
        models = [ChatGPTModel("gpt-5.4", "GPT-5.4")]

        StudyApp._finish_chatgpt_account_refresh(  # type: ignore[arg-type]
            app,
            6,
            signed_in,
            models,
        )

        self.assertEqual(app._settings_chatgpt_model.get(), "gpt-5.4")
        self.assertEqual(app.settings.chatgpt_model, "gpt-5.4")
        self.assertEqual(app._chatgpt_model_combobox.values, ("gpt-5.4",))

    def test_chatgpt_login_starts_in_a_background_thread(self) -> None:
        app = StartLoginApp()

        with patch("konspekt.app.threading.Thread") as thread_class:
            StudyApp._start_chatgpt_login(app)  # type: ignore[arg-type]

        self.assertTrue(app._chatgpt_login_active)
        self.assertEqual(app.busy_states, [True])
        self.assertIn("Заверши вход", app.statuses[0])
        self.assertTrue(thread_class.call_args.kwargs["daemon"])
        thread_class.return_value.start.assert_called_once_with()

    def test_chatgpt_worker_logs_in_then_generates_and_delivers_on_tk_queue(
        self,
    ) -> None:
        recording = BBBRecording(
            meeting_id="meeting-chatgpt-worker",
            source_url="https://example.test/playback?meetingId=meeting-chatgpt-worker",
            title="ChatGPT worker lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        app = DeferredChatGPTApp()
        signed_out = ChatGPTAccountStatus(False, None, None)
        signed_in = ChatGPTAccountStatus(True, "student@example.test", "plus")
        models = [ChatGPTModel("gpt-5.5", "GPT-5.5")]
        generated = ChatGPTGenerationResult(
            Path("C:/library/meeting-chatgpt-worker/lesson.md"),
            "gpt-5.5",
        )

        def generate(
            _: BBBRecording,
            model: str,
            **kwargs: object,
        ) -> ChatGPTGenerationResult:
            self.assertEqual(model, "gpt-5.5")
            progress = kwargs["progress"]
            assert callable(progress)
            progress(15, "Создаём lesson.md…")
            return generated

        with (
            patch("konspekt.app.chatgpt_account_status", return_value=signed_out),
            patch("konspekt.app.login_with_chatgpt", return_value=signed_in) as login,
            patch("konspekt.app.list_chatgpt_models", return_value=models),
            patch(
                "konspekt.app.generate_lesson_with_chatgpt",
                side_effect=generate,
            ),
        ):
            StudyApp._chatgpt_generation_worker(  # type: ignore[arg-type]
                app,
                recording,
                "gpt-5.5",
                operation_id=7,
                account_operation_id=4,
            )

        self.assertEqual(app.progress, [])
        self.assertEqual(app.generation_results, [])
        while app.callbacks:
            app.callbacks.pop(0)()

        login.assert_called_once_with()
        self.assertEqual(app.errors, [])
        self.assertEqual(app.account_results, [(signed_in, models, "")])
        self.assertEqual(app.generation_results, [(recording, generated)])
        self.assertEqual([percent for percent, _ in app.progress], [10, 20, 45, 52])

    def test_chatgpt_account_worker_restores_state_on_error(self) -> None:
        app = AccountRefreshApp()
        app._chatgpt_account_operation_id = 10
        app._chatgpt_login_active = True

        with patch(
            "konspekt.app.login_with_chatgpt",
            side_effect=ChatGPTAccountError("Codex app-server слишком долго не отвечает."),
        ):
            StudyApp._chatgpt_account_worker(  # type: ignore[arg-type]
                app,
                should_login=True,
                operation_id=10,
            )

        # Ensure callback executed and restored state
        self.assertFalse(app._chatgpt_login_active)
        self.assertIn("слишком долго не отвечает", app.status)

    def test_chatgpt_generation_worker_restores_state_on_account_error(self) -> None:
        recording = BBBRecording(
            meeting_id="meeting-chatgpt-err",
            source_url="https://example.test/playback?meetingId=meeting-chatgpt-err",
            title="ChatGPT error lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )
        app = DeferredChatGPTApp()

        with patch(
            "konspekt.app.chatgpt_account_status",
            side_effect=ChatGPTAccountError("Codex app-server неожиданно завершил работу."),
        ):
            StudyApp._chatgpt_generation_worker(  # type: ignore[arg-type]
                app,
                recording,
                "gpt-5.5",
                operation_id=7,
                account_operation_id=4,
            )

        while app.callbacks:
            app.callbacks.pop(0)()
        self.assertEqual(len(app.errors), 1)
        self.assertIn("неожиданно завершил работу", app.errors[0])


if __name__ == "__main__":
    unittest.main()
