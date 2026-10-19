"""Talk to Claude through the official Anthropic SDK."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .job_runner import CancellationToken, JobCancelledError
from .settings import DEFAULT_CLAUDE_MODEL

if TYPE_CHECKING:
    import anthropic

__all__ = ["DEFAULT_CLAUDE_MODEL", "ClaudeApiError", "ClaudeReply", "stream_claude_text"]

DEFAULT_MAX_TOKENS = 64_000
# With ``fallbacks: "default"`` a request declined by the safety classifiers is
# re-run server-side on the model Anthropic recommends for that category.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_DEFAULT_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
# Older models reject ``output_config.effort``; they simply run without it.
_EFFORT_MODEL_PREFIXES = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
)

TextCallback = Callable[[str], None]


class ClaudeApiError(RuntimeError):
    """Claude could not produce a usable answer."""


@dataclass(frozen=True)
class ClaudeReply:
    text: str
    model: str
    stop_reason: str


def stream_claude_text(
    api_key: str,
    model: str,
    messages: Sequence[dict[str, Any]],
    *,
    system: str = "",
    effort: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    on_text: TextCallback | None = None,
    cancellation_token: CancellationToken | None = None,
    client: Any | None = None,
) -> ClaudeReply:
    """Stream one reply, passing text deltas to ``on_text`` as they arrive."""

    # The SDK takes a couple of seconds to import, so load it on first use.
    import anthropic

    if not api_key.strip() and client is None:
        raise ClaudeApiError("Сначала добавь API-ключ Claude в настройках.")
    if cancellation_token is not None:
        cancellation_token.check_cancelled()

    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": list(messages),
    }
    if system:
        params["system"] = system
    if effort and model.startswith(_EFFORT_MODEL_PREFIXES):
        params["output_config"] = {"effort": effort}
    if model in _DEFAULT_FALLBACK_MODELS:
        params["betas"] = [_FALLBACK_BETA]
        params["fallbacks"] = "default"

    owns_client = client is None
    if client is None:
        client = anthropic.Anthropic(
            api_key=api_key.strip(),
            timeout=anthropic.Timeout(600.0, connect=20.0),
        )
    try:
        with client.beta.messages.stream(**params) as stream:
            if cancellation_token is not None:
                cancellation_token.register(stream.close)
            for text in stream.text_stream:
                if on_text is not None:
                    on_text(text)
            final = stream.get_final_message()
    except anthropic.APIError as exc:
        if cancellation_token is not None and cancellation_token.is_cancelled:
            raise JobCancelledError("Операция была отменена пользователем.") from exc
        raise _user_error(exc, model) from exc
    except Exception as exc:
        # Closing the stream on cancellation surfaces as a transport error.
        if cancellation_token is not None and cancellation_token.is_cancelled:
            raise JobCancelledError("Операция была отменена пользователем.") from exc
        raise
    finally:
        if owns_client:
            client.close()

    if cancellation_token is not None:
        cancellation_token.check_cancelled()
    stop_reason = str(final.stop_reason or "")
    if stop_reason == "refusal":
        raise ClaudeApiError(
            "Claude отказался отвечать на этот запрос. Попробуй переформулировать его."
        )
    text = "".join(block.text for block in final.content if block.type == "text")
    return ClaudeReply(text=text, model=str(final.model or model), stop_reason=stop_reason)


def _user_error(exc: anthropic.APIError, model: str) -> ClaudeApiError:
    import anthropic

    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ClaudeApiError("API-ключ Claude не принят. Проверь ключ в настройках.")
    if isinstance(exc, anthropic.NotFoundError):
        return ClaudeApiError(f"Модель «{model}» не найдена. Проверь название модели в настройках.")
    if isinstance(exc, anthropic.RateLimitError):
        return ClaudeApiError(
            "Claude отклонил запрос из-за лимита. Подожди немного или проверь аккаунт Anthropic."
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return ClaudeApiError("Claude слишком долго не отвечает. Повтори запрос.")
    if isinstance(exc, anthropic.APIConnectionError):
        return ClaudeApiError("Не удалось подключиться к Claude API. Проверь интернет.")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            return ClaudeApiError("Claude временно перегружен или недоступен. Повтори позже.")
        detail = _error_detail(exc)
        suffix = f": {detail}" if detail else ". Проверь модель и настройки."
        return ClaudeApiError(f"Claude отклонил запрос (код {exc.status_code}){suffix}")
    return ClaudeApiError("Claude API вернул ошибку. Повтори запрос.")


def _error_detail(exc: anthropic.APIStatusError) -> str:
    """The provider's own explanation, e.g. a too low credit balance."""

    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"].strip()[:300]
    return ""
