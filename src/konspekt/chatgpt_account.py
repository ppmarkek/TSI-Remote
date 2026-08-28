"""ChatGPT account access through the official local Codex CLI surfaces.

Authentication and model discovery use ``codex app-server``. Lesson generation
uses an ephemeral, read-only ``codex exec`` process and never automates or
scrapes the ChatGPT website.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bbb_import import BBBRecording
from .local_pipeline import default_lecture_directory
from .outbound_context import OutboundContextError, _validate_outbound_text


class ChatGPTAccountError(RuntimeError):
    """A ChatGPT account or Codex CLI operation could not be completed."""


@dataclass(frozen=True)
class ChatGPTAccountStatus:
    signed_in: bool
    email: str | None
    plan_type: str | None


@dataclass(frozen=True)
class ChatGPTModel:
    slug: str
    display_name: str


@dataclass(frozen=True)
class ChatGPTGenerationResult:
    lesson_path: Path
    model: str


ProgressCallback = Callable[[int, str], None]

_AUTH_URL_ENV = "KONSPEKT_CHATGPT_AUTH_URL"
_REQUEST_TIMEOUT_SECONDS = 30.0
_LOGIN_TIMEOUT_SECONDS = 10 * 60.0
_GENERATION_TIMEOUT_SECONDS = 30 * 60
_KEYRING_SETTING = 'cli_auth_credentials_store = "keyring"'
_MODEL_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_CONFIG_LOCK = threading.Lock()


def chatgpt_account_status() -> ChatGPTAccountStatus:
    """Return the ChatGPT account attached to Konspekt's private Codex home."""

    with _open_app_server() as server:
        response = server.request(
            "account/read",
            {"refreshToken": False},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    return _account_status_from_response(response)


def list_chatgpt_models() -> list[ChatGPTModel]:
    """Return models exposed by the signed-in account's Codex app-server."""

    with _open_app_server() as server:
        account = server.request(
            "account/read",
            {"refreshToken": False},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if not _account_status_from_response(account).signed_in:
            raise ChatGPTAccountError("Сначала войди в ChatGPT, чтобы загрузить доступные модели.")

        models: list[ChatGPTModel] = []
        seen_slugs: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(100):
            params: dict[str, object] = {"includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            response = server.request(
                "model/list",
                params,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            data = response.get("data")
            if not isinstance(data, list):
                raise ChatGPTAccountError(
                    "Codex вернул неожиданный список моделей. Обнови Codex CLI."
                )
            for item in data:
                if not isinstance(item, dict) or item.get("hidden") is True:
                    continue
                slug = _clean_string(item.get("model")) or _clean_string(item.get("id"))
                if not slug or slug in seen_slugs:
                    continue
                display_name = _clean_string(item.get("displayName")) or slug
                seen_slugs.add(slug)
                models.append(ChatGPTModel(slug=slug, display_name=display_name))

            next_cursor = _clean_string(response.get("nextCursor"))
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise ChatGPTAccountError(
                    "Codex зациклился при загрузке моделей. Перезапусти приложение."
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise ChatGPTAccountError(
                "Codex вернул слишком много страниц моделей. Перезапусти приложение."
            )

    if not models:
        raise ChatGPTAccountError("Для этого аккаунта ChatGPT не найдено доступных моделей.")
    return models


def login_with_chatgpt() -> ChatGPTAccountStatus:
    """Sign in through a short-lived, internal pywebview helper process."""

    with _open_app_server() as server:
        response = server.request(
            "account/login/start",
            {"type": "chatgpt"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if response.get("type") != "chatgpt":
            raise ChatGPTAccountError("Эта версия Codex не смогла начать вход через ChatGPT.")
        login_id = _clean_string(response.get("loginId"))
        auth_url = _clean_string(response.get("authUrl"))
        if not login_id or not auth_url:
            raise ChatGPTAccountError("Codex не вернул данные для входа. Обнови Codex CLI.")

        helper = _start_auth_helper(auth_url)
        completed: dict[str, Any] | None = None
        try:
            completed = server.wait_for_notification(
                "account/login/completed",
                predicate=lambda params: params.get("loginId") in {None, login_id},
                timeout=_LOGIN_TIMEOUT_SECONDS,
                helper_process=helper,
            )
        except _AuthWindowClosed as exc:
            _cancel_login(server, login_id)
            raise ChatGPTAccountError("Окно входа было закрыто до завершения авторизации.") from exc
        except TimeoutError as exc:
            _cancel_login(server, login_id)
            raise ChatGPTAccountError(
                "Вход в ChatGPT не завершился за 10 минут. Повтори попытку."
            ) from exc
        finally:
            _stop_process(helper)

        if not completed or completed.get("success") is not True:
            raise ChatGPTAccountError("ChatGPT не подтвердил вход. Повтори попытку в новом окне.")

        account = server.request(
            "account/read",
            {"refreshToken": False},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        status = _account_status_from_response(account)
        if not status.signed_in:
            raise ChatGPTAccountError(
                "Вход завершился, но Codex ещё не видит аккаунт ChatGPT. Повтори попытку."
            )
        return status


def generate_lesson_with_chatgpt(
    recording: BBBRecording,
    model: str,
    directory: Path | None = None,
    progress: ProgressCallback | None = None,
) -> ChatGPTGenerationResult:
    """Generate ``lesson.md`` with an isolated Codex exec invocation.

    The lesson prompt and locally prepared context are sent through stdin. Only
    Codex's ``--output-last-message`` file is accepted as the generated lesson,
    and the existing lesson is replaced atomically after validation.
    """

    selected_model = _validated_model_slug(model)
    target = directory or default_lecture_directory(recording)
    notify = progress or _ignore_progress
    notify(5, "Проверяем подготовленные материалы лекции…")

    prompt = _read_lesson_input(target / "lesson-prompt.md", "инструкция урока")
    context = _without_source_details(
        _read_lesson_input(target / "lesson-context.md", "контекст лекции")
    )
    try:
        forbidden = [recording.meeting_id, recording.source_url]
        _validate_outbound_text("chatgpt_context", context, forbidden)
        _validate_outbound_text("chatgpt_prompt", prompt, forbidden)
    except OutboundContextError as exc:
        raise ChatGPTAccountError(
            f"Ошибка проверки безопасности передаваемых данных: {exc}"
        ) from exc
    codex_executable = _codex_command()
    stdin_payload = (
        "Работай только с переданным ниже текстом. Не запускай команды и не читай "
        "другие файлы. Верни только итоговый урок в Markdown.\n\n"
        "<lesson_instructions>\n"
        f"{prompt.rstrip()}\n"
        "</lesson_instructions>\n\n"
        "<lecture_context>\n"
        f"{context.rstrip()}\n"
        "</lecture_context>\n"
    )

    try:
        target.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".lesson-chatgpt-",
            suffix=".tmp",
            dir=target,
        )
        os.close(descriptor)
    except OSError as exc:
        raise ChatGPTAccountError(
            "Не удалось подготовить файл для нового конспекта. Проверь доступ к папке лекции."
        ) from exc

    temporary_path = Path(temporary_name)
    lesson_path = target / "lesson.md"
    command = [
        codex_executable,
        "exec",
        "--ephemeral",
        "-c",
        'cli_auth_credentials_store="keyring"',
        "-c",
        'web_search="disabled"',
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "--disable",
        "apps",
        "--disable",
        "multi_agent",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--model",
        selected_model,
        "-o",
        str(temporary_path),
        "-",
    ]

    notify(15, "ChatGPT создаёт структурированный конспект…")
    try:
        result = subprocess.run(
            command,
            input=stdin_payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GENERATION_TIMEOUT_SECONDS,
            env=_codex_environment(),
            cwd=str(target),
            creationflags=_hidden_creation_flags(),
        )
        if result.returncode != 0:
            raise ChatGPTAccountError(
                "Codex не смог создать конспект. Проверь вход в ChatGPT и повтори попытку."
            )
        try:
            lesson = temporary_path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError) as exc:
            raise ChatGPTAccountError(
                "Codex завершил работу, но конспект не удалось прочитать."
            ) from exc
        if not lesson:
            raise ChatGPTAccountError(
                "Codex завершил работу без текста конспекта. Повтори попытку."
            )
        notify(92, "Сохраняем готовый конспект…")
        temporary_path.write_text(f"{lesson}\n", encoding="utf-8")
        temporary_path.replace(lesson_path)
    except subprocess.TimeoutExpired as exc:
        raise ChatGPTAccountError(
            "ChatGPT не завершил конспект за 30 минут. Исходные материалы сохранены."
        ) from exc
    except OSError as exc:
        raise ChatGPTAccountError(
            "Не удалось запустить Codex CLI или сохранить конспект. Проверь установку Codex."
        ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    notify(100, "Конспект ChatGPT готов.")
    return ChatGPTGenerationResult(lesson_path=lesson_path, model=selected_model)


class _StreamClosed:
    pass


@dataclass(frozen=True)
class _ProtocolFailure:
    pass


class _AuthWindowClosed(RuntimeError):
    pass


class _AppServerSession:
    """Small synchronous JSONL client for Codex app-server stdio."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ChatGPTAccountError("Не удалось подключиться к Codex app-server.")
        self._process = process
        self._stdin = process.stdin
        self._messages: queue.Queue[dict[str, Any] | _StreamClosed | _ProtocolFailure]
        self._messages = queue.Queue()
        self._pending: deque[dict[str, Any]] = deque()
        self._next_request_id = 0
        self._write_lock = threading.Lock()
        threading.Thread(
            target=self._read_stdout,
            args=(process.stdout,),
            name="konspekt-codex-app-server",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_stderr,
            args=(process.stderr,),
            name="konspekt-codex-app-server-stderr",
            daemon=True,
        ).start()

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "konspekt",
                    "title": "Konspekt",
                    "version": "0.1.0",
                }
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        self._send({"method": "initialized"})

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        pending_count = len(self._pending)
        for _ in range(pending_count):
            message = self._pending.popleft()
            if message.get("id") == request_id:
                return self._result_from_response(message, method)
            self._pending.append(message)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChatGPTAccountError("Codex app-server слишком долго не отвечает.")
            try:
                message = self._next_message(remaining)
            except queue.Empty:
                raise ChatGPTAccountError("Codex app-server слишком долго не отвечает.")
            if message.get("id") != request_id:
                self._pending.append(message)
                continue
            return self._result_from_response(message, method)

    @staticmethod
    def _result_from_response(message: dict[str, Any], method: str) -> dict[str, Any]:
        if message.get("error") is not None:
            err = message.get("error")
            err_msg = ""
            if isinstance(err, dict):
                err_msg = str(err.get("message") or err)
            else:
                err_msg = str(err)
            suffix = f": {err_msg}" if err_msg else "."
            raise ChatGPTAccountError(f"Codex app-server отклонил запрос {method}{suffix}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ChatGPTAccountError(f"Codex app-server вернул неверный ответ на {method}.")
        return result

    def wait_for_notification(
        self,
        method: str,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        helper_process: subprocess.Popen[Any],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            pending_count = len(self._pending)
            for _ in range(pending_count):
                message = self._pending.popleft()
                params = message.get("params")
                if (
                    message.get("method") == method
                    and isinstance(params, dict)
                    and predicate(params)
                ):
                    return params
                self._pending.append(message)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            try:
                message = self._next_message(min(remaining, 0.25))
            except queue.Empty:
                if helper_process.poll() is not None:
                    raise _AuthWindowClosed
                continue
            params = message.get("params")
            if message.get("method") == method and isinstance(params, dict) and predicate(params):
                return params
            self._pending.append(message)

    def _send(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._write_lock:
                self._stdin.write(f"{encoded}\n")
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise ChatGPTAccountError("Соединение с Codex app-server прервано.") from exc

    def _next_message(self, timeout: float) -> dict[str, Any]:
        item = self._messages.get(timeout=timeout)
        if isinstance(item, _ProtocolFailure):
            raise ChatGPTAccountError("Codex app-server вернул повреждённый ответ.")
        if isinstance(item, _StreamClosed):
            raise ChatGPTAccountError("Codex app-server неожиданно завершил работу.")
        return item

    def _read_stdout(self, stream: Any) -> None:
        try:
            for raw_line in iter(stream.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._messages.put(_ProtocolFailure())
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
                else:
                    self._messages.put(_ProtocolFailure())
        finally:
            self._messages.put(_StreamClosed())

    @staticmethod
    def _drain_stderr(stream: Any) -> None:
        # Drain the pipe to avoid deadlocks. OAuth URLs and provider output are
        # deliberately not retained or surfaced.
        try:
            for _ in iter(stream.readline, ""):
                pass
        except (OSError, ValueError):
            pass


@contextmanager
def _open_app_server() -> Iterator[_AppServerSession]:
    command = [_codex_command(), "app-server", "--stdio"]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=_codex_environment(),
            creationflags=_hidden_creation_flags(),
        )
    except OSError as exc:
        raise ChatGPTAccountError(
            "Codex CLI не найден. Установи или обнови Codex и повтори попытку."
        ) from exc

    try:
        session = _AppServerSession(process)
        session.initialize()
        yield session
    finally:
        _stop_process(process)


def _start_auth_helper(auth_url: str) -> subprocess.Popen[Any]:
    environment = os.environ.copy()
    environment[_AUTH_URL_ENV] = auth_url
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--chatgpt-auth-window"]
    else:
        command = [
            sys.executable,
            "-m",
            "konspekt",
            "--chatgpt-auth-window",
        ]
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=_hidden_creation_flags(),
        )
    except OSError as exc:
        raise ChatGPTAccountError("Не удалось открыть защищённое окно входа в ChatGPT.") from exc


def _cancel_login(server: _AppServerSession, login_id: str) -> None:
    try:
        server.request(
            "account/login/cancel",
            {"loginId": login_id},
            timeout=5.0,
        )
    except ChatGPTAccountError:
        pass


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _account_status_from_response(response: dict[str, Any]) -> ChatGPTAccountStatus:
    account = response.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        return ChatGPTAccountStatus(signed_in=False, email=None, plan_type=None)
    return ChatGPTAccountStatus(
        signed_in=True,
        email=_clean_string(account.get("email")),
        plan_type=_clean_string(account.get("planType")),
    )


def _codex_home() -> Path:
    # Keep app-server state in the same platform-owned root as the library.
    # Never manufacture a Windows ``~/AppData`` path on macOS.
    from .platform_services import PlatformAppPaths

    configured = os.environ.get("KONSPEKT_CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    if "LOCALAPPDATA" in os.environ:
        return Path(os.environ["LOCALAPPDATA"]) / "Konspekt" / "Codex"
    return PlatformAppPaths().data_dir / "Codex"


def _codex_environment() -> dict[str, str]:
    home = _ensure_codex_home()
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(home)
    environment.setdefault("NO_COLOR", "1")
    return environment


def _ensure_codex_home() -> Path:
    home = _codex_home()
    config_path = home / "config.toml"
    with _CONFIG_LOCK:
        try:
            home.mkdir(parents=True, exist_ok=True)
            existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
            lines = existing.splitlines()
            setting_pattern = re.compile(r"^\s*cli_auth_credentials_store\s*=", re.IGNORECASE)
            filtered = [line for line in lines if not setting_pattern.match(line)]
            desired_lines = [_KEYRING_SETTING]
            if filtered:
                desired_lines.extend(["", *filtered])
            desired = "\n".join(desired_lines).rstrip() + "\n"
            if existing == desired:
                return home
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="config-",
                suffix=".tmp",
                dir=home,
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                temporary.write_text(desired, encoding="utf-8")
                temporary.replace(config_path)
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, UnicodeError) as exc:
            raise ChatGPTAccountError(
                "Не удалось подготовить защищённое хранилище входа ChatGPT."
            ) from exc
    return home


def _codex_command() -> str:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidates: list[str | None] = []
    if os.name == "nt":
        candidates.extend(
            [
                str(bundle_root / "codex.exe"),
                str(bundle_root / "codex.cmd"),
                shutil.which("codex.cmd"),
                shutil.which("codex.exe"),
            ]
        )
    candidates.append(shutil.which("codex"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ChatGPTAccountError("Codex CLI не найден. Установи или обнови Codex и повтори попытку.")


def _read_lesson_input(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChatGPTAccountError(
            f"Не удалось прочитать {label}. Сначала подготовь материалы лекции."
        ) from exc
    if not value.strip():
        raise ChatGPTAccountError(f"Файл «{label}» пуст. Сначала повтори подготовку материалов.")
    return value


def _without_source_details(markdown: str) -> str:
    """Remove BBB source URL and meeting id before content leaves the machine."""

    kept: list[str] = []
    skipping = False
    for line in markdown.splitlines():
        if line.strip() == "## Источник":
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip() + "\n"


def _validated_model_slug(value: str) -> str:
    model = str(value or "").strip()
    if not _MODEL_SLUG.fullmatch(model):
        raise ChatGPTAccountError("Выбери модель из списка ChatGPT.")
    return model


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _hidden_creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _ignore_progress(_: int, __: str) -> None:
    pass
