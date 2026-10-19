"""A simple multi-turn chat with the configured text API."""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from .api_generation import (
    DEEPSEEK_CHAT_URL,
    GEMINI_CHAT_URL,
    OPENAI_RESPONSES_URL,
    ApiGenerationError,
    _http_error,
    _response_json_or_empty,
)
from .atomic_io import AtomicIOError, atomic_write_text
from .claude_api import ClaudeApiError, stream_claude_text
from .diagnostics import record_exception
from .job_runner import CancellationToken, JobCancelledError
from .markdown_html import render_markdown
from .settings import AppSettings

CHAT_SCHEMA_VERSION = 1
MAX_MESSAGE_CHARS = 100_000
# Older turns are dropped first once the conversation outgrows this budget.
MAX_HISTORY_CHARS = 300_000
TITLE_LENGTH = 60
CHAT_SYSTEM_PROMPT = (
    "Ты — дружелюбный и точный помощник по учёбе в приложении «Конспект». "
    "Отвечай на языке пользователя, ясно и по делу. Используй Markdown "
    "(заголовки, списки, таблицы, блоки кода), когда это делает ответ понятнее."
)

TextCallback = Callable[[str], None]
ChatStreamer = Callable[..., "ChatReply"]


class ChatError(RuntimeError):
    """The chat could not complete the requested action."""


@dataclass(frozen=True)
class ChatReply:
    text: str
    model: str


# ===================================================================== storage
class ChatStore:
    """Conversations kept in one JSON file next to the library."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        chats: list[dict[str, Any]] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("chats"), list):
                chats = [chat for chat in raw["chats"] if _valid_chat(chat)]
        except (OSError, ValueError):
            chats = []
        self._data = {"schema_version": CHAT_SCHEMA_VERSION, "chats": chats}
        return self._data

    def _save(self) -> None:
        try:
            atomic_write_text(
                self.path,
                json.dumps(self._load(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (AtomicIOError, OSError) as exc:
            raise ChatError("Не удалось сохранить чат на диск.") from exc

    def _find(self, chat_id: str) -> dict[str, Any]:
        for chat in self._load()["chats"]:
            if chat["id"] == chat_id:
                return chat
        raise ChatError("Чат не найден. Возможно, он уже удалён.")

    def list_chats(self) -> list[dict[str, Any]]:
        with self._lock:
            chats = sorted(self._load()["chats"], key=lambda c: c["updated_at"], reverse=True)
            return [
                {
                    "id": chat["id"],
                    "title": chat["title"],
                    "updated_at": chat["updated_at"],
                    "message_count": len(chat["messages"]),
                }
                for chat in chats
            ]

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._find(chat_id))

    def add_message(
        self,
        chat_id: str | None,
        role: str,
        content: str,
        *,
        model: str = "",
        stopped: bool = False,
    ) -> dict[str, Any]:
        """Append a message, starting a new chat when ``chat_id`` is empty."""

        with self._lock:
            now = _now()
            if chat_id:
                chat = self._find(chat_id)
            else:
                chat = {
                    "id": _new_id(),
                    "title": _title_from(content),
                    "created_at": now,
                    "updated_at": now,
                    "messages": [],
                }
                self._load()["chats"].append(chat)
            message: dict[str, Any] = {
                "id": _new_id(),
                "role": role,
                "content": content,
                "created_at": now,
            }
            if model:
                message["model"] = model
            if stopped:
                message["stopped"] = True
            chat["messages"].append(message)
            chat["updated_at"] = now
            self._save()
            return copy.deepcopy(chat)

    def drop_trailing_assistant(self, chat_id: str) -> dict[str, Any]:
        """Forget the last answer so it can be generated again."""

        with self._lock:
            chat = self._find(chat_id)
            while chat["messages"] and chat["messages"][-1]["role"] == "assistant":
                chat["messages"].pop()
            if not chat["messages"]:
                raise ChatError("В этом чате нет вопроса, на который можно ответить заново.")
            self._save()
            return copy.deepcopy(chat)

    def rename(self, chat_id: str, title: str) -> dict[str, Any]:
        cleaned = " ".join(str(title).split())[:120]
        if not cleaned:
            raise ChatError("Название чата не может быть пустым.")
        with self._lock:
            chat = self._find(chat_id)
            chat["title"] = cleaned
            self._save()
            return copy.deepcopy(chat)

    def delete(self, chat_id: str) -> None:
        with self._lock:
            data = self._load()
            remaining = [chat for chat in data["chats"] if chat["id"] != chat_id]
            if len(remaining) == len(data["chats"]):
                raise ChatError("Чат не найден. Возможно, он уже удалён.")
            data["chats"] = remaining
            self._save()


def _valid_chat(chat: Any) -> bool:
    if not isinstance(chat, dict):
        return False
    if not all(
        isinstance(chat.get(key), str) for key in ("id", "title", "created_at", "updated_at")
    ):
        return False
    messages = chat.get("messages")
    if not isinstance(messages, list):
        return False
    chat["messages"] = [
        message
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and isinstance(message.get("id"), str)
    ]
    return True


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _title_from(text: str) -> str:
    line = " ".join(re.sub(r"[#*_`>]+", " ", text).split())
    if len(line) <= TITLE_LENGTH:
        return line or "Новый чат"
    return line[: TITLE_LENGTH - 1].rstrip() + "…"


def history_for_api(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """The newest turns that fit the budget, alternating roles, user first."""

    merged: list[dict[str, str]] = []
    for message in messages:
        content = message["content"].strip()
        if not content:
            continue
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"] += "\n\n" + content
        else:
            merged.append({"role": message["role"], "content": content})

    kept: list[dict[str, str]] = []
    used = 0
    for message in reversed(merged):
        used += len(message["content"])
        if kept and used > MAX_HISTORY_CHARS:
            break
        kept.append(message)
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    if kept and len(kept[0]["content"]) > MAX_HISTORY_CHARS:
        kept[0]["content"] = kept[0]["content"][-MAX_HISTORY_CHARS:]
    return kept


def message_payload(message: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": message["id"],
        "role": message["role"],
        "content": message["content"],
        "created_at": message.get("created_at", ""),
        "model": message.get("model", ""),
        "stopped": bool(message.get("stopped")),
    }
    if message["role"] == "assistant":
        payload["html"] = render_markdown(message["content"], timestamp_tag="span").html
    return payload


# =================================================================== providers
def stream_chat_reply(
    settings: AppSettings,
    messages: Sequence[dict[str, str]],
    *,
    on_text: TextCallback,
    cancellation_token: CancellationToken,
    session: Any | None = None,
) -> ChatReply:
    """Stream an answer from whichever API provider is configured."""

    if not settings.api_configured:
        raise ChatError("Чтобы пользоваться чатом, добавь API-ключ и модель в настройках.")
    if settings.api_provider == "claude":
        try:
            reply = stream_claude_text(
                settings.api_key,
                settings.api_model,
                messages,
                system=CHAT_SYSTEM_PROMPT,
                on_text=on_text,
                cancellation_token=cancellation_token,
            )
        except ClaudeApiError as exc:
            raise ChatError(str(exc)) from exc
        return ChatReply(text=reply.text, model=reply.model)

    if settings.api_provider == "openai":
        endpoint = OPENAI_RESPONSES_URL
        payload: dict[str, Any] = {
            "model": settings.api_model,
            "instructions": CHAT_SYSTEM_PROMPT,
            "input": list(messages),
            "store": False,
            "stream": True,
        }
        read_delta = _openai_delta
    elif settings.api_provider in {"deepseek", "gemini"}:
        endpoint = DEEPSEEK_CHAT_URL if settings.api_provider == "deepseek" else GEMINI_CHAT_URL
        payload = {
            "model": settings.api_model,
            "messages": [{"role": "system", "content": CHAT_SYSTEM_PROMPT}, *messages],
            "stream": True,
        }
        if settings.api_model.startswith("deepseek-v4"):
            payload["thinking"] = {"type": "disabled"}
        read_delta = _deepseek_delta
    else:
        raise ChatError("Выбран неподдерживаемый API-провайдер.")

    text = _stream_sse(
        endpoint,
        settings.api_key,
        payload,
        read_delta,
        on_text=on_text,
        cancellation_token=cancellation_token,
        session=session,
    )
    return ChatReply(text=text, model=settings.api_model)


def _stream_sse(
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
    read_delta: Callable[[dict[str, Any]], str],
    *,
    on_text: TextCallback,
    cancellation_token: CancellationToken,
    session: Any | None,
) -> str:
    cancellation_token.check_cancelled()
    owns_client = session is None
    client = session or requests.Session()
    response: Any | None = None
    chunks: list[str] = []

    def close() -> None:
        if response is not None:
            response.close()
        client.close()

    cancellation_token.register(close)
    try:
        response = client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            stream=True,
            timeout=(20, 300),
        )
        if response.status_code >= 400:
            raise _http_error(response.status_code, _response_json_or_empty(response))
        for raw_line in response.iter_lines():
            cancellation_token.check_cancelled()
            # Event streams are UTF-8; requests would guess Latin-1 for them.
            line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            delta = read_delta(event)
            if delta:
                chunks.append(delta)
                on_text(delta)
    except (ChatError, JobCancelledError):
        raise
    except ApiGenerationError as exc:
        raise ChatError(str(exc)) from exc
    except requests.Timeout as exc:
        if cancellation_token.is_cancelled:
            raise JobCancelledError("Операция была отменена пользователем.") from exc
        raise ChatError("API слишком долго не отвечает. Повтори запрос.") from exc
    except (requests.RequestException, OSError) as exc:
        if cancellation_token.is_cancelled:
            raise JobCancelledError("Операция была отменена пользователем.") from exc
        raise ChatError(
            "Не удалось подключиться к API. Проверь интернет и повтори запрос."
        ) from exc
    except Exception as exc:
        # Closing the connection mid-read surfaces as an arbitrary transport error.
        if cancellation_token.is_cancelled:
            raise JobCancelledError("Операция была отменена пользователем.") from exc
        raise
    finally:
        if response is not None:
            response.close()
        if owns_client:
            client.close()
    cancellation_token.check_cancelled()
    return "".join(chunks)


def _openai_delta(event: dict[str, Any]) -> str:
    kind = event.get("type")
    if kind == "response.output_text.delta":
        delta = event.get("delta")
        return delta if isinstance(delta, str) else ""
    if kind in {"error", "response.failed"}:
        error = event.get("error")
        if not isinstance(error, dict):
            response = event.get("response")
            error = response.get("error") if isinstance(response, dict) else None
        message = error.get("message") if isinstance(error, dict) else ""
        detail = f": {message.strip()[:300]}" if isinstance(message, str) and message else "."
        raise ChatError(f"OpenAI не смог ответить{detail}")
    return ""


def _deepseek_delta(event: dict[str, Any]) -> str:
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


# ======================================================================= runs
class ChatRun:
    """One answer being written in the background."""

    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.token = CancellationToken()
        self.text = ""
        self.done = False
        self.error = ""
        self.stopped = False
        self._changed = threading.Condition()

    def append(self, delta: str) -> None:
        with self._changed:
            self.text += delta
            self._changed.notify_all()

    def finish(self, *, error: str = "", stopped: bool = False) -> None:
        with self._changed:
            self.done = True
            self.error = error
            self.stopped = stopped
            self._changed.notify_all()

    def wait(self, seen: int, timeout: float) -> None:
        with self._changed:
            self._changed.wait_for(lambda: self.done or len(self.text) > seen, timeout)


class ChatService:
    """Starts, streams, stops and stores chat answers."""

    def __init__(
        self,
        store: ChatStore,
        settings: Callable[[], AppSettings],
        *,
        streamer: ChatStreamer = stream_chat_reply,
    ) -> None:
        self.store = store
        self._settings = settings
        self._streamer = streamer
        self._runs: dict[str, ChatRun] = {}
        self._lock = threading.Lock()

    def chat_payload(self, chat_id: str) -> dict[str, Any]:
        chat = self.store.get_chat(chat_id)
        with self._lock:
            run = self._runs.get(chat_id)
        return {
            "id": chat["id"],
            "title": chat["title"],
            "created_at": chat["created_at"],
            "updated_at": chat["updated_at"],
            "messages": [message_payload(message) for message in chat["messages"]],
            "running": run is not None and not run.done,
            "error": run.error if run is not None and run.done else "",
        }

    def send(self, chat_id: str | None, text: str) -> dict[str, Any]:
        content = str(text).replace("\r\n", "\n").strip()
        if not content:
            raise ChatError("Напиши сообщение.")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ChatError("Сообщение слишком длинное. Сократи его или раздели на части.")
        settings = self._configured_settings()
        if chat_id:
            self._ensure_idle(chat_id)
        chat = self.store.add_message(chat_id or None, "user", content)
        self._start(chat["id"], settings)
        return self.chat_payload(chat["id"])

    def retry(self, chat_id: str) -> dict[str, Any]:
        settings = self._configured_settings()
        self._ensure_idle(chat_id)
        self.store.drop_trailing_assistant(chat_id)
        self._start(chat_id, settings)
        return self.chat_payload(chat_id)

    def stop(self, chat_id: str) -> None:
        with self._lock:
            run = self._runs.get(chat_id)
        if run is not None and not run.done:
            run.token.cancel()

    def poll(self, chat_id: str, seen: int = 0, timeout: float = 15.0) -> dict[str, Any]:
        """Wait until the answer grows past ``seen`` characters or finishes."""

        with self._lock:
            run = self._runs.get(chat_id)
        if run is None:
            return {"running": False, "chat": self.chat_payload(chat_id)}
        run.wait(max(0, int(seen)), max(0.0, min(float(timeout), 30.0)))
        if not run.done:
            text = run.text
            return {
                "running": True,
                "length": len(text),
                "html": render_markdown(text, timestamp_tag="span").html,
            }
        with self._lock:
            if self._runs.get(chat_id) is run:
                del self._runs[chat_id]
        return {
            "running": False,
            "error": run.error,
            "stopped": run.stopped,
            "chat": self.chat_payload(chat_id),
        }

    def delete(self, chat_id: str) -> None:
        self.stop(chat_id)
        with self._lock:
            self._runs.pop(chat_id, None)
        self.store.delete(chat_id)

    def is_running(self, chat_id: str) -> bool:
        with self._lock:
            run = self._runs.get(chat_id)
        return run is not None and not run.done

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            run.token.cancel()

    # ---------------------------------------------------------------- helpers
    def _configured_settings(self) -> AppSettings:
        settings = self._settings()
        if not settings.api_configured:
            raise ChatError("Чтобы пользоваться чатом, добавь API-ключ и модель в настройках.")
        return settings

    def _ensure_idle(self, chat_id: str) -> None:
        if self.is_running(chat_id):
            raise ChatError("Дождись окончания ответа или останови его.")

    def _start(self, chat_id: str, settings: AppSettings) -> None:
        run = ChatRun(chat_id)
        with self._lock:
            self._runs[chat_id] = run
        thread = threading.Thread(
            target=self._work,
            args=(run, settings),
            name=f"konspekt-chat-{chat_id}",
            daemon=True,
        )
        thread.start()

    def _work(self, run: ChatRun, settings: AppSettings) -> None:
        try:
            history = history_for_api(self.store.get_chat(run.chat_id)["messages"])
            reply = self._streamer(
                settings,
                history,
                on_text=run.append,
                cancellation_token=run.token,
            )
            text = (reply.text or run.text).strip()
            if not text:
                raise ChatError("Модель вернула пустой ответ. Попробуй ещё раз.")
            self.store.add_message(run.chat_id, "assistant", text, model=reply.model)
            run.finish()
        except JobCancelledError:
            partial = run.text.strip()
            error = ""
            if partial:
                try:
                    self.store.add_message(
                        run.chat_id, "assistant", partial, model=settings.api_model, stopped=True
                    )
                except ChatError as exc:
                    error = str(exc)
            run.finish(stopped=True, error=error)
        except ChatError as exc:
            run.finish(error=str(exc))
        except Exception as exc:  # pragma: no cover - logged for diagnostics
            record_exception("chat.reply", exc)
            run.finish(error="Не удалось получить ответ. Повтори запрос.")
