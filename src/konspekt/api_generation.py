"""Create a finished lesson through an explicitly configured text API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .bbb_import import BBBRecording
from .lesson_output import SavedLesson, save_generated_lesson
from .local_pipeline import default_lecture_directory
from .outbound_context import OutboundContextError, _validate_outbound_text
from .settings import AppSettings

ProgressCallback = Callable[[int, str], None]


class ApiGenerationError(RuntimeError):
    """A text API could not create a usable lesson."""


@dataclass(frozen=True)
class ApiLessonResult:
    saved_lesson: SavedLesson
    provider: str
    model: str


def generate_lesson_via_api(
    recording: BBBRecording,
    settings: AppSettings,
    *,
    directory: Path | None = None,
    progress: ProgressCallback | None = None,
    session: Any | None = None,
) -> ApiLessonResult:
    """Send only the prepared text context and save the returned Markdown."""

    if not settings.api_configured:
        raise ApiGenerationError("Сначала добавь API-ключ и модель в настройках.")

    target = directory or default_lecture_directory(recording)
    context_path = target / "lesson-context.md"
    prompt_path = target / "lesson-prompt.md"
    if not context_path.is_file() or not prompt_path.is_file():
        raise ApiGenerationError("Сначала собери пакет контекста для этой лекции.")

    try:
        context = _without_source_details(context_path.read_text(encoding="utf-8"))
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ApiGenerationError("Не удалось прочитать локальный пакет контекста.") from exc
    if not context.strip() or not prompt:
        raise ApiGenerationError("Локальный пакет контекста пуст или повреждён.")

    try:
        forbidden = [recording.meeting_id, recording.source_url]
        _validate_outbound_text("api_context", context, forbidden)
        _validate_outbound_text("api_prompt", prompt, forbidden)
    except OutboundContextError as exc:
        raise ApiGenerationError(
            f"Ошибка проверки безопасности передаваемых данных: {exc}"
        ) from exc

    notify = progress or _do_nothing
    notify(8, "Проверяем локальный текстовый пакет…")
    if settings.api_provider == "openai":
        endpoint = "https://api.openai.com/v1/responses"
        payload = _openai_payload(settings.api_model, prompt, context)
    elif settings.api_provider == "deepseek":
        endpoint = "https://api.deepseek.com/chat/completions"
        payload = _deepseek_payload(settings.api_model, prompt, context)
    else:
        raise ApiGenerationError("Выбран неподдерживаемый API-провайдер.")

    notify(
        24,
        f"Отправляем только текстовый контекст в {settings.provider_label}…",
    )
    owns_client = session is None
    client = session or requests.Session()
    response: Any | None = None
    try:
        response = client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(20, 300),
        )
    except requests.Timeout as exc:
        if owns_client:
            client.close()
        raise ApiGenerationError(
            "API слишком долго не отвечает. Повтори запрос: локальные материалы сохранены."
        ) from exc
    except requests.RequestException as exc:
        if owns_client:
            client.close()
        raise ApiGenerationError(
            "Не удалось подключиться к API. Проверь интернет и повтори запрос."
        ) from exc
    try:
        if response.status_code >= 400:
            raise _http_error(response.status_code, _response_json_or_empty(response))
        body = _response_json(response)

        notify(88, "Ответ получен. Проверяем и сохраняем конспект…")
        if settings.api_provider == "openai":
            markdown = _openai_output_text(body)
        else:
            markdown = _deepseek_output_text(body)
        markdown = _clean_markdown(markdown)
        if not markdown:
            raise ApiGenerationError("API вернул пустой ответ. Локальные материалы не изменены.")

        saved = save_generated_lesson(recording, markdown, directory=target)
        notify(100, "Конспект создан и сохранён локально.")
        return ApiLessonResult(
            saved_lesson=saved,
            provider=settings.provider_label,
            model=settings.api_model,
        )
    finally:
        close_response = getattr(response, "close", None)
        if callable(close_response):
            close_response()
        if owns_client:
            close_client = getattr(client, "close", None)
            if callable(close_client):
                close_client()


def _openai_payload(model: str, prompt: str, context: str) -> dict[str, Any]:
    return {
        "model": model,
        "instructions": (
            "Создай точный учебный конспект по переданному контексту. "
            "Верни только готовый Markdown без пояснений о процессе."
        ),
        "input": f"{prompt}\n\n{context}",
        "store": False,
    }


def _deepseek_payload(model: str, prompt: str, context: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Создай точный учебный конспект. Верни только готовый Markdown "
                    "и не добавляй факты, которых нет в контексте."
                ),
            },
            {"role": "user", "content": f"{prompt}\n\n{context}"},
        ],
        "stream": False,
    }
    if model.startswith("deepseek-v4"):
        payload["thinking"] = {"type": "disabled"}
    return payload


def _response_json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except (TypeError, ValueError) as exc:
        raise ApiGenerationError("API вернул ответ в неизвестном формате.") from exc
    if not isinstance(body, dict):
        raise ApiGenerationError("API вернул ответ в неизвестном формате.")
    return body


def _response_json_or_empty(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _http_error(status_code: int, body: dict[str, Any]) -> ApiGenerationError:
    if status_code in {401, 403}:
        return ApiGenerationError("API-ключ не принят. Проверь ключ в настройках.")
    if status_code == 429:
        return ApiGenerationError(
            "API отклонил запрос из-за лимита или баланса. Проверь аккаунт провайдера."
        )
    if status_code in {408, 504}:
        return ApiGenerationError("API не успел обработать запрос. Попробуй ещё раз.")
    if status_code >= 500:
        return ApiGenerationError(
            "Сервис API временно недоступен. Локальные материалы сохранены — повтори позже."
        )

    return ApiGenerationError(
        f"API отклонил запрос (код {status_code}). Проверь модель и настройки."
    )


def _openai_output_text(body: dict[str, Any]) -> str:
    convenience = body.get("output_text")
    if isinstance(convenience, str) and convenience.strip():
        return convenience
    chunks: list[str] = []
    output = body.get("output", [])
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks)


def _deepseek_output_text(body: dict[str, Any]) -> str:
    choices = body.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _without_source_details(markdown: str) -> str:
    """Remove the BBB URL and meeting id from API-bound context."""

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


def _clean_markdown(markdown: str) -> str:
    cleaned = markdown.replace("\r\n", "\n").strip()
    if not cleaned.startswith("```") or not cleaned.endswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if len(lines) < 3:
        return cleaned
    if lines[0].strip().lower() in {"```", "```md", "```markdown"}:
        return "\n".join(lines[1:-1]).strip()
    return cleaned


def _do_nothing(_: int, __: str) -> None:
    return None
