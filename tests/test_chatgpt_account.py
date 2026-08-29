from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from konspekt.bbb_import import BBBRecording
from konspekt.chatgpt_account import (
    ChatGPTAccountError,
    ChatGPTAccountStatus,
    ChatGPTGenerationResult,
    ChatGPTModel,
    _AppServerSession,
    _codex_environment,
    _start_auth_helper,
    chatgpt_account_status,
    generate_lesson_with_chatgpt,
    list_chatgpt_models,
    login_with_chatgpt,
)


class FakeServer:
    def __init__(self, responses: dict[str, list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.notification = {
            "loginId": "login-1",
            "success": True,
            "error": None,
        }

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        del timeout
        self.calls.append((method, params))
        configured = self.responses.get(method)
        if not configured:
            raise AssertionError(f"unexpected app-server request: {method}")
        return configured.pop(0)

    def wait_for_notification(
        self,
        method: str,
        *,
        predicate: Any,
        timeout: float,
        helper_process: Any,
    ) -> dict[str, Any]:
        del timeout, helper_process
        if method != "account/login/completed" or not predicate(self.notification):
            raise AssertionError("unexpected notification wait")
        return self.notification


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.terminated or self.killed else None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


class FakeWireProcess:
    def __init__(self, stdout: str) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")


@contextmanager
def opened(server: FakeServer) -> Iterator[FakeServer]:
    yield server


class ChatGPTAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = BBBRecording(
            meeting_id="chatgpt-lecture",
            source_url="https://bbb.example.test/playback?meetingId=chatgpt-lecture",
            title="ChatGPT lecture",
            imported_at="2026-07-15T10:00:00+00:00",
            audio_video_url="https://bbb.example.test/video/webcams.webm",
            screen_video_url=None,
            slides=(),
        )

    def test_reads_chatgpt_account_status(self) -> None:
        server = FakeServer(
            {
                "account/read": [
                    {
                        "account": {
                            "type": "chatgpt",
                            "email": "student@example.test",
                            "planType": "plus",
                        },
                        "requiresOpenaiAuth": True,
                    }
                ]
            }
        )
        with patch(
            "konspekt.chatgpt_account._open_app_server",
            side_effect=lambda: opened(server),
        ):
            status = chatgpt_account_status()

        self.assertEqual(
            status,
            ChatGPTAccountStatus(
                signed_in=True,
                email="student@example.test",
                plan_type="plus",
            ),
        )
        self.assertEqual(
            server.calls,
            [("account/read", {"refreshToken": False})],
        )

    def test_non_chatgpt_account_is_not_treated_as_signed_in(self) -> None:
        server = FakeServer(
            {
                "account/read": [
                    {
                        "account": {"type": "apiKey"},
                        "requiresOpenaiAuth": False,
                    }
                ]
            }
        )
        with patch(
            "konspekt.chatgpt_account._open_app_server",
            side_effect=lambda: opened(server),
        ):
            status = chatgpt_account_status()
        self.assertEqual(status, ChatGPTAccountStatus(False, None, None))

    def test_lists_models_from_result_data_and_paginates(self) -> None:
        server = FakeServer(
            {
                "account/read": [
                    {
                        "account": {
                            "type": "chatgpt",
                            "email": "student@example.test",
                            "planType": "plus",
                        }
                    }
                ],
                "model/list": [
                    {
                        "data": [
                            {
                                "id": "stable-id",
                                "model": "gpt-5.2-codex",
                                "displayName": "GPT-5.2 Codex",
                                "isDefault": True,
                            },
                            {
                                "id": "hidden-id",
                                "model": "hidden-model",
                                "displayName": "Hidden",
                                "hidden": True,
                                "isDefault": False,
                            },
                        ],
                        "nextCursor": "page-2",
                    },
                    {
                        "data": [
                            {
                                "id": "fallback-id",
                                "displayName": "Fallback model",
                                "isDefault": False,
                            },
                            {
                                "id": "duplicate",
                                "model": "gpt-5.2-codex",
                                "displayName": "Duplicate",
                                "isDefault": False,
                            },
                        ]
                    },
                ],
            }
        )
        with patch(
            "konspekt.chatgpt_account._open_app_server",
            side_effect=lambda: opened(server),
        ):
            models = list_chatgpt_models()

        self.assertEqual(
            models,
            [
                ChatGPTModel("gpt-5.2-codex", "GPT-5.2 Codex"),
                ChatGPTModel("fallback-id", "Fallback model"),
            ],
        )
        model_calls = [call for call in server.calls if call[0] == "model/list"]
        self.assertEqual(model_calls[0][1], {"includeHidden": False})
        self.assertEqual(
            model_calls[1][1],
            {"includeHidden": False, "cursor": "page-2"},
        )

    def test_login_uses_only_stable_chatgpt_params(self) -> None:
        server = FakeServer(
            {
                "account/login/start": [
                    {
                        "type": "chatgpt",
                        "loginId": "login-1",
                        "authUrl": "https://auth.openai.com/oauth/authorize?state=secret",
                    }
                ],
                "account/read": [
                    {
                        "account": {
                            "type": "chatgpt",
                            "email": "student@example.test",
                            "planType": "plus",
                        }
                    }
                ],
            }
        )
        helper = FakeProcess()
        with (
            patch(
                "konspekt.chatgpt_account._open_app_server",
                side_effect=lambda: opened(server),
            ),
            patch(
                "konspekt.chatgpt_account._start_auth_helper",
                return_value=helper,
            ) as start_helper,
        ):
            status = login_with_chatgpt()

        self.assertTrue(status.signed_in)
        self.assertEqual(
            server.calls[0],
            ("account/login/start", {"type": "chatgpt"}),
        )
        start_helper.assert_called_once_with("https://auth.openai.com/oauth/authorize?state=secret")
        self.assertTrue(helper.terminated)

    def test_auth_url_is_not_put_on_helper_command_line(self) -> None:
        captured: dict[str, Any] = {}
        helper = FakeProcess()

        def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
            captured["command"] = command
            captured.update(kwargs)
            return helper

        auth_url = "https://auth.openai.com/oauth/authorize?state=secret"
        with (
            patch("konspekt.chatgpt_account.subprocess.Popen", side_effect=fake_popen),
            patch("konspekt.chatgpt_account.sys.executable", r"C:\Python\python.exe"),
            patch.object(
                __import__("konspekt.chatgpt_account", fromlist=["sys"]).sys,
                "frozen",
                False,
                create=True,
            ),
        ):
            _start_auth_helper(auth_url)

        self.assertEqual(
            captured["command"],
            [
                r"C:\Python\python.exe",
                "-m",
                "konspekt",
                "--chatgpt-auth-window",
            ],
        )
        self.assertNotIn(auth_url, " ".join(captured["command"]))
        self.assertEqual(captured["env"]["KONSPEKT_CHATGPT_AUTH_URL"], auth_url)

    def test_frozen_helper_reuses_application_executable(self) -> None:
        captured: list[str] = []

        def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
            del kwargs
            captured.extend(command)
            return FakeProcess()

        module = __import__("konspekt.chatgpt_account", fromlist=["sys"])
        with (
            patch("konspekt.chatgpt_account.subprocess.Popen", side_effect=fake_popen),
            patch("konspekt.chatgpt_account.sys.executable", r"C:\App\Konspekt.exe"),
            patch.object(module.sys, "frozen", True, create=True),
        ):
            _start_auth_helper("https://auth.openai.com/oauth/authorize")
        self.assertEqual(
            captured,
            [r"C:\App\Konspekt.exe", "--chatgpt-auth-window"],
        )

    def test_app_server_wire_uses_jsonl_without_jsonrpc_field(self) -> None:
        wire_output = "\n".join(
            [
                json.dumps({"id": 0, "result": {"serverInfo": {}}}),
                json.dumps(
                    {
                        "method": "account/updated",
                        "params": {"authMode": "chatgpt"},
                    }
                ),
                json.dumps(
                    {
                        "id": 1,
                        "result": {
                            "account": None,
                            "requiresOpenaiAuth": True,
                        },
                    }
                ),
                "",
            ]
        )
        process = FakeWireProcess(wire_output)
        session = _AppServerSession(process)  # type: ignore[arg-type]
        session.initialize()
        response = session.request(
            "account/read",
            {"refreshToken": False},
            timeout=1,
        )

        sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        self.assertEqual(response["account"], None)
        self.assertEqual(sent[0]["method"], "initialize")
        self.assertEqual(sent[1], {"method": "initialized"})
        self.assertEqual(sent[2]["method"], "account/read")
        self.assertTrue(all("jsonrpc" not in message for message in sent))

    def test_codex_environment_is_app_specific_and_uses_keyring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            local_app_data = Path(temporary)
            config = local_app_data / "Konspekt" / "Codex" / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text("[features]\nexample = true\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": str(local_app_data),
                    "CODEX_HOME": r"C:\Users\student\.codex",
                },
                clear=False,
            ):
                environment = _codex_environment()

            saved = config.read_text(encoding="utf-8")

        self.assertEqual(
            environment["CODEX_HOME"],
            str(local_app_data / "Konspekt" / "Codex"),
        )
        self.assertIn('cli_auth_credentials_store = "keyring"', saved)
        self.assertIn("[features]\nexample = true", saved)

    def test_generation_uses_isolated_codex_exec_and_atomic_output(self) -> None:
        progress: list[tuple[int, str]] = []
        captured: dict[str, Any] = {}

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: Any) -> None:
                captured["command"] = command
                captured.update(kwargs)
                self.returncode = 0
                output = Path(command[command.index("-o") + 1])
                output.write_text("# Новый конспект", encoding="utf-8")

            def communicate(
                self, input: str | None = None, timeout: float | None = None
            ) -> tuple[str, str]:
                captured["input"] = input
                return "", ""

            def poll(self) -> int | None:
                return self.returncode

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-prompt.md").write_text(
                "Сделай структурированный урок.", encoding="utf-8"
            )
            (directory / "lesson-context.md").write_text(
                "Важный локальный контекст.", encoding="utf-8"
            )
            (directory / "lesson.md").write_text("# Старый", encoding="utf-8")
            with (
                patch("konspekt.chatgpt_account._codex_command", return_value="codex.cmd"),
                patch(
                    "konspekt.chatgpt_account._codex_environment",
                    return_value={"CODEX_HOME": "private-home"},
                ),
                patch("konspekt.chatgpt_account.subprocess.Popen", side_effect=FakePopen),
            ):
                result = generate_lesson_with_chatgpt(
                    self.recording,
                    "gpt-5.2-codex",
                    directory=directory,
                    progress=lambda percent, message: progress.append((percent, message)),
                )
            saved = (directory / "lesson.md").read_text(encoding="utf-8")
            temporary_outputs = list(directory.glob(".lesson-chatgpt-*.tmp"))

        self.assertEqual(
            result,
            ChatGPTGenerationResult(result.lesson_path, "gpt-5.2-codex"),
        )
        self.assertEqual(saved, "# Новый конспект\n")
        self.assertEqual(temporary_outputs, [])
        command = captured["command"]
        self.assertEqual(command[:2], ["codex.cmd", "exec"])
        self.assertIn("--ephemeral", command)
        config_values = [
            command[index + 1] for index, item in enumerate(command[:-1]) if item == "-c"
        ]
        self.assertIn('cli_auth_credentials_store="keyring"', config_values)
        self.assertIn('web_search="disabled"', config_values)
        disabled_features = [
            command[index + 1] for index, item in enumerate(command[:-1]) if item == "--disable"
        ]
        self.assertEqual(
            disabled_features,
            ["shell_tool", "unified_exec", "apps", "multi_agent"],
        )
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[-1], "-")
        self.assertIn("Сделай структурированный урок.", captured["input"])
        self.assertIn("Важный локальный контекст.", captured["input"])
        self.assertEqual(captured["env"], {"CODEX_HOME": "private-home"})
        self.assertEqual(captured["stdout"], subprocess.DEVNULL)
        self.assertEqual(captured["stderr"], subprocess.DEVNULL)
        self.assertEqual(progress[-1][0], 100)

    def test_generation_removes_bbb_source_details_from_stdin(self) -> None:
        captured_input = ""

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: Any) -> None:
                self.command = command
                self.returncode = 0
                output = Path(command[command.index("-o") + 1])
                output.write_text("# Lesson", encoding="utf-8")

            def communicate(
                self, input: str | None = None, timeout: float | None = None
            ) -> tuple[str, str]:
                nonlocal captured_input
                captured_input = input or ""
                return "", ""

            def poll(self) -> int | None:
                return self.returncode

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-prompt.md").write_text("Prompt", encoding="utf-8")
            (directory / "lesson-context.md").write_text(
                "# Context\n\n"
                "## Источник\n"
                "- BBB: https://private.example.test/playback\n"
                "- ID: `private-meeting-id`\n\n"
                "## Транскрипция\n\n"
                "Useful lecture text.\n",
                encoding="utf-8",
            )
            with (
                patch("konspekt.chatgpt_account._codex_command", return_value="codex.cmd"),
                patch(
                    "konspekt.chatgpt_account._codex_environment",
                    return_value={"CODEX_HOME": "private-home"},
                ),
                patch("konspekt.chatgpt_account.subprocess.Popen", side_effect=FakePopen),
            ):
                generate_lesson_with_chatgpt(
                    self.recording,
                    "gpt-5.2-codex",
                    directory=directory,
                )

        self.assertIn("Useful lecture text.", captured_input)
        self.assertNotIn("private.example.test", captured_input)
        self.assertNotIn("private-meeting-id", captured_input)

    def test_failed_generation_preserves_existing_lesson(self) -> None:
        class FailingPopen:
            def __init__(self, *_: Any, **__: Any) -> None:
                self.returncode = 1

            def communicate(self, *_: Any, **__: Any) -> tuple[str, str]:
                return "", ""

            def poll(self) -> int | None:
                return self.returncode

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-prompt.md").write_text("Prompt", encoding="utf-8")
            (directory / "lesson-context.md").write_text("Context", encoding="utf-8")
            lesson_path = directory / "lesson.md"
            lesson_path.write_text("# Existing\n", encoding="utf-8")
            with (
                patch("konspekt.chatgpt_account._codex_command", return_value="codex.cmd"),
                patch(
                    "konspekt.chatgpt_account._codex_environment",
                    return_value={"CODEX_HOME": "private-home"},
                ),
                patch(
                    "konspekt.chatgpt_account.subprocess.Popen",
                    side_effect=FailingPopen,
                ),
            ):
                with self.assertRaises(ChatGPTAccountError):
                    generate_lesson_with_chatgpt(
                        self.recording,
                        "gpt-5.2-codex",
                        directory=directory,
                    )
            saved = lesson_path.read_text(encoding="utf-8")
            temporary_outputs = list(directory.glob(".lesson-chatgpt-*.tmp"))

        self.assertEqual(saved, "# Existing\n")
        self.assertEqual(temporary_outputs, [])

    def test_queue_empty_and_timeout_normalize_to_chatgpt_account_error(self) -> None:
        import queue

        process = FakeWireProcess("")
        session = _AppServerSession(process)  # type: ignore[arg-type]
        session._messages.get = MagicMock(side_effect=queue.Empty)  # type: ignore[method-assign]

        with self.assertRaises(ChatGPTAccountError) as exc_info:
            session.request("test/timeout", {}, timeout=0.01)

        self.assertIn("слишком долго не отвечает", str(exc_info.exception))

    def test_unexpected_eof_and_stream_closed_normalizes_to_chatgpt_account_error(self) -> None:
        process = FakeWireProcess("")
        session = _AppServerSession(process)  # type: ignore[arg-type]
        # Allow background thread to hit EOF and put _StreamClosed
        import time

        time.sleep(0.05)

        with self.assertRaises(ChatGPTAccountError) as exc_info:
            session.request("test/eof", {}, timeout=1.0)

        self.assertIn("неожиданно завершил работу", str(exc_info.exception))

    def test_app_server_error_response_normalizes_to_chatgpt_account_error(self) -> None:
        wire = '{"id":0,"error":{"message":"custom error from codex"}}\n'
        process = FakeWireProcess(wire)
        session = _AppServerSession(process)  # type: ignore[arg-type]

        with self.assertRaises(ChatGPTAccountError) as exc_info:
            session.request("test/err", {}, timeout=1.0)

        self.assertIn("custom error from codex", str(exc_info.exception))

    def test_generation_respects_cancellation_token(self) -> None:
        from konspekt.job_runner import CancellationToken, JobCancelledError

        token = CancellationToken()
        token.cancel()

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "lesson-prompt.md").write_text("Prompt", encoding="utf-8")
            (directory / "lesson-context.md").write_text("Context", encoding="utf-8")

            with self.assertRaises(JobCancelledError):
                generate_lesson_with_chatgpt(
                    self.recording,
                    "gpt-5.2-codex",
                    directory=directory,
                    cancellation_token=token,
                )


if __name__ == "__main__":
    unittest.main()
