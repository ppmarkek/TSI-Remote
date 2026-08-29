"""Strict outbound context boundary and sanitization for external AI providers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


class OutboundContextError(RuntimeError):
    """Data cannot be prepared for an external provider due to policy or format errors."""


@dataclass(frozen=True)
class OutboundSlide:
    identifier: str
    text: str


@dataclass(frozen=True)
class OutboundScreenNote:
    timestamp_seconds: float
    text: str


@dataclass(frozen=True)
class OutboundTimelineBlock:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class OutboundConsentSummary:
    provider: str
    files_or_fields: tuple[str, ...]
    estimated_size_bytes: int
    character_count: int
    summary_text: str


_FORBIDDEN_PATH_PATTERN = re.compile(
    r"(?:(?:\\\\|//)[A-Za-z0-9_.-]+[/\\][^\s\"'>)]+|[A-Za-z]:[/\\][^\s\"'>)]+|"
    r"/(?:Users|home|AppData|System|Library|var|tmp|private|etc|opt|usr|bin|root|mnt|media|Volumes|Applications|data|build|temp|scratch|local)[/\\][^\s\"'>)]+|"
    r"/(?:[a-zA-Z0-9._-]+/){2,}[^\s\"'>)]*)",
    re.IGNORECASE,
)
_FORBIDDEN_SECRET_PATTERN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{22,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|aws_secret_access_key\s*[:=]\s*[^\s&,\"']+|"
    r"sk-[A-Za-z0-9_-]{10,}|sk-ant-[A-Za-z0-9_-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]+|"
    r"Bearer\s+[A-Za-z0-9_.-]+|SECRET-[^\s]+|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|token|auth[_-]?token)\s*[:=]\s*[^\s&,\"']+|"
    r"(?:[?&])(?:token|secret|key|access_token|api_key)=[^\s&]+|token=DO_NOT_SEND)",
    re.IGNORECASE,
)
_FORBIDDEN_URL_PATTERN = re.compile(
    r"(?:\b[a-z][a-z0-9+.-]{1,31}://[^\s\"'>)]+|\b(?:meetingId|meeting_id)\s*[:=]\s*[^\s&,\"']+|"
    r"\?[^\s\"'>)]*(?:meetingId|meeting_id|token|secret|key)=[^\s\"'>)]*)",
    re.IGNORECASE,
)
_FORBIDDEN_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Za-z0-9_-]{20,}-\d{4,}|[A-Fa-f0-9]{24,}|secret-meeting-[^\s\"'>)]+|meeting_[a-zA-Z0-9_-]+|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b",
    re.IGNORECASE,
)

PROVIDER_CONTEXT_LIMITS: dict[str, dict[str, int]] = {
    "openai": {"max_chars": 400_000, "max_bytes": 1_000_000},
    "deepseek": {"max_chars": 250_000, "max_bytes": 600_000},
    "chatgpt": {"max_chars": 400_000, "max_bytes": 1_000_000},
    "deepseek_web": {"max_chars": 200_000, "max_bytes": 500_000},
}


def validate_provider_context_limits(
    provider: str,
    character_count: int,
    size_bytes: int,
) -> None:
    """Ensure context payload does not exceed provider-specific safe bounds."""
    key = provider.strip().lower().replace(" ", "_")
    limits = PROVIDER_CONTEXT_LIMITS.get(key)
    if not limits:
        limits = {"max_chars": 400_000, "max_bytes": 1_000_000}

    max_chars = limits["max_chars"]
    max_bytes = limits["max_bytes"]

    if character_count > max_chars or size_bytes > max_bytes:
        size_kb = max(1, size_bytes // 1024)
        max_kb = max_bytes // 1024
        raise OutboundContextError(
            f"Размер контекста ({character_count:,} символов, ~{size_kb} КБ) "
            f"превышает допустимый лимит для {provider} ({max_chars:,} символов, ~{max_kb} КБ). "
            f"Уменьши длительность лекции или отключи OCR для сокращения объёма."
        )


@dataclass(frozen=True)
class OutboundContext:
    """Immutable, strictly sanitized lecture context for external transmission."""

    title: str
    slides: tuple[OutboundSlide, ...]
    screen_notes: tuple[OutboundScreenNote, ...]
    transcript_blocks: tuple[OutboundTimelineBlock, ...]
    instructions: str
    format_version: int = 1
    estimated_size_bytes: int = 0
    character_count: int = 0

    @property
    def sanitized_text(self) -> str:
        return self.render_markdown()

    def render_markdown(self) -> str:
        """Render markdown representation without source URLs or meeting IDs."""
        lines = [
            f"# Контекст лекции: {self.title}",
            "",
            "> Этот файл собран локально. Он не является готовым конспектом: передай его в выбранный чат вместе с `lesson-prompt.md`.",
            "",
            "## Текст со слайдов",
            "",
        ]
        if self.slides:
            for slide in self.slides:
                label = slide.identifier.replace("_", " ")
                lines.append(f"### {label}")
                lines.append(slide.text or "Текст на слайде не извлечён.")
                lines.append("")
        else:
            lines.extend(["Текст слайдов недоступен.", ""])

        lines.extend(["## Текст на экране", ""])
        if self.screen_notes:
            for note in self.screen_notes:
                lines.append(f"- **{_format_timestamp(note.timestamp_seconds)}** — {note.text}")
            lines.append("")
        else:
            lines.extend(["OCR-заметки с экрана недоступны.", ""])

        lines.extend(["## Транскрипция по времени", ""])
        if self.transcript_blocks:
            for block in self.transcript_blocks:
                lines.append(
                    f"### {_format_timestamp(block.start_seconds)} — {_format_timestamp(block.end_seconds)}"
                )
                lines.extend([block.text, ""])
        else:
            lines.append("Речь в записи не была распознана.")

        rendered = "\n".join(lines).rstrip() + "\n"
        _validate_outbound_text("rendered_markdown", rendered, ())
        return rendered

    def render_prompt(self) -> str:
        """Render the user prompt instructions."""
        prompt = _render_lesson_prompt(self.title)
        _validate_outbound_text("rendered_prompt", prompt, ())
        return prompt

    def to_dict(self) -> dict[str, Any]:
        """Serialize into a safe, versioned dictionary."""
        payload = {
            "schema_version": self.format_version,
            "lecture": {
                "title": self.title,
            },
            "slides": [asdict(slide) for slide in self.slides],
            "screen_notes": [asdict(note) for note in self.screen_notes],
            "transcript_blocks": [asdict(block) for block in self.transcript_blocks],
        }
        return payload

    def consent_summary(self, provider_label: str) -> OutboundConsentSummary:
        """Generate human-readable consent summary before any handoff."""
        fields = (
            f"Транскрипция ({len(self.transcript_blocks)} блоков)",
            f"Слайды ({len(self.slides)} шт.)",
            f"OCR с экрана ({len(self.screen_notes)} заметок)",
            "Учебные инструкции (lesson-prompt.md)",
        )
        size_kb = max(1, self.estimated_size_bytes // 1024)
        summary_text = (
            f"Провайдер: {provider_label}\n"
            f"Передаваемые данные: {len(self.transcript_blocks)} блоков речи, "
            f"{len(self.slides)} слайдов, {len(self.screen_notes)} OCR-заметок.\n"
            f"Примерный объём: ~{size_kb} КБ ({self.character_count:,} символов).\n\n"
            f"Внимание: BigBlueButton URL, идентификатор встречи и системные пути исключены."
        )
        return OutboundConsentSummary(
            provider=provider_label,
            files_or_fields=fields,
            estimated_size_bytes=self.estimated_size_bytes,
            character_count=self.character_count,
            summary_text=summary_text,
        )


def build_outbound_context(
    title: str,
    *,
    slides: Sequence[Any] = (),
    screen_notes: Sequence[Any] = (),
    transcript_blocks: Sequence[Any] = (),
    transcript_text: str = "",
    slides_text: str = "",
    ocr_notes_text: str = "",
    instructions: str | None = None,
    meeting_id: str | None = None,
    source_url: str | None = None,
    forbidden_tokens: Sequence[str] = (),
) -> OutboundContext:
    """Build and validate an immutable OutboundContext from lecture artifacts."""

    clean_title = _normalise_text(title)
    if not clean_title:
        raise OutboundContextError("Название лекции не может быть пустым.")

    forbidden = list(forbidden_tokens)
    if meeting_id and meeting_id.strip():
        forbidden.append(meeting_id.strip())
    if source_url and source_url.strip():
        forbidden.append(source_url.strip())

    # Validate title
    _validate_outbound_text("title", clean_title, forbidden)

    all_slides = list(slides)
    if slides_text.strip():
        all_slides.append({"identifier": "slide-1", "text": slides_text.strip()})

    all_screen_notes = list(screen_notes)
    if ocr_notes_text.strip():
        all_screen_notes.append({"timestamp_seconds": 0.0, "text": ocr_notes_text.strip()})

    all_transcript_blocks = list(transcript_blocks)
    if transcript_text.strip():
        all_transcript_blocks.append({"start": 0.0, "end": 0.0, "text": transcript_text.strip()})

    # Process and validate slides
    clean_slides: list[OutboundSlide] = []
    seen_slide_text: set[str] = set()
    for index, slide in enumerate(all_slides, start=1):
        if hasattr(slide, "identifier"):
            identifier = str(slide.identifier or f"slide-{index}")
            text = str(getattr(slide, "text", ""))
        elif isinstance(slide, dict):
            identifier = str(slide.get("identifier") or f"slide-{index}")
            text = str(slide.get("text", ""))
        else:
            continue
        clean_text = _normalise_text(text)
        fingerprint = clean_text.casefold()
        if fingerprint and fingerprint in seen_slide_text:
            continue
        if fingerprint:
            seen_slide_text.add(fingerprint)
        _validate_outbound_text(f"slide_{identifier}", clean_text, forbidden)
        _validate_outbound_text(f"slide_id_{identifier}", identifier, forbidden)
        clean_slides.append(OutboundSlide(identifier=identifier, text=clean_text))

    # Process and validate screen notes
    clean_notes: list[OutboundScreenNote] = []
    seen_note_text: set[str] = set()
    for note in all_screen_notes:
        if hasattr(note, "timestamp_seconds"):
            timestamp = float(note.timestamp_seconds)
            text = str(getattr(note, "text", ""))
        elif isinstance(note, dict):
            try:
                timestamp = float(note.get("timestamp_seconds", 0.0))
            except (TypeError, ValueError):
                continue
            text = str(note.get("text", ""))
        else:
            continue
        clean_text = _normalise_text(text)
        fingerprint = clean_text.casefold()
        if not clean_text or fingerprint in seen_note_text:
            continue
        seen_note_text.add(fingerprint)
        _validate_outbound_text(f"screen_note_{timestamp}", clean_text, forbidden)
        clean_notes.append(OutboundScreenNote(timestamp_seconds=timestamp, text=clean_text))

    # Process and validate transcript blocks
    clean_blocks: list[OutboundTimelineBlock] = []
    for block in all_transcript_blocks:
        if hasattr(block, "start_seconds") and hasattr(block, "end_seconds"):
            start = float(block.start_seconds)
            end = float(block.end_seconds)
            text = str(getattr(block, "text", ""))
        elif isinstance(block, dict):
            try:
                start = float(block.get("start", block.get("start_seconds", 0.0)))
                end = float(block.get("end", block.get("end_seconds", 0.0)))
            except (TypeError, ValueError):
                continue
            text = str(block.get("text", ""))
        else:
            continue
        clean_text = _normalise_text(text)
        if clean_text:
            _validate_outbound_text(f"transcript_{start}_{end}", clean_text, forbidden)
            clean_blocks.append(
                OutboundTimelineBlock(
                    start_seconds=start, end_seconds=max(start, end), text=clean_text
                )
            )

    prompt_instructions = instructions or _render_lesson_prompt(clean_title)
    _validate_outbound_text("instructions", prompt_instructions, forbidden)

    # Compute size
    total_chars = (
        len(clean_title)
        + sum(len(s.identifier) + len(s.text) for s in clean_slides)
        + sum(len(n.text) for n in clean_notes)
        + sum(len(b.text) for b in clean_blocks)
        + len(prompt_instructions)
    )
    est_bytes = (
        len(clean_title.encode("utf-8"))
        + sum(len(s.identifier.encode("utf-8")) + len(s.text.encode("utf-8")) for s in clean_slides)
        + sum(len(n.text.encode("utf-8")) for n in clean_notes)
        + sum(len(b.text.encode("utf-8")) for b in clean_blocks)
        + len(prompt_instructions.encode("utf-8"))
    )

    context = OutboundContext(
        title=clean_title,
        slides=tuple(clean_slides),
        screen_notes=tuple(clean_notes),
        transcript_blocks=tuple(clean_blocks),
        instructions=prompt_instructions,
        format_version=1,
        estimated_size_bytes=est_bytes,
        character_count=total_chars,
    )

    # Final sanity check on rendered output
    rendered_md = context.render_markdown()
    _validate_outbound_text("final_rendered_markdown", rendered_md, forbidden)

    return context


def redact_sensitive_strings(text: str) -> str:
    """Redact URLs, secrets, absolute paths, and meeting IDs from text."""
    redacted = _FORBIDDEN_SECRET_PATTERN.sub("[REDACTED_SECRET]", text)
    redacted = _FORBIDDEN_URL_PATTERN.sub("[REDACTED_URL]", redacted)
    redacted = _FORBIDDEN_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    redacted = _FORBIDDEN_IDENTIFIER_PATTERN.sub("[REDACTED_ID]", redacted)
    return redacted


def _validate_outbound_text(
    field_name: str,
    text: str,
    forbidden_values: Sequence[str],
) -> None:
    """Check text against forbidden explicit values and privacy leak patterns."""

    if not text:
        return

    for val in forbidden_values:
        if val and val in text:
            raise OutboundContextError(
                f"Обнаружена утечка конфиденциальных данных в поле {field_name}: {val}"
            )

    if _FORBIDDEN_SECRET_PATTERN.search(text):
        raise OutboundContextError(f"Обнаружен токен, секрет или API-ключ в поле {field_name}.")

    if _FORBIDDEN_PATH_PATTERN.search(text):
        raise OutboundContextError(f"Обнаружен локальный абсолютный путь в поле {field_name}.")

    if _FORBIDDEN_URL_PATTERN.search(text):
        raise OutboundContextError(f"Обнаружен URL или ссылка на запись в поле {field_name}.")

    if _FORBIDDEN_IDENTIFIER_PATTERN.search(text):
        raise OutboundContextError(f"Обнаружен идентификатор или UUID в поле {field_name}.")


def _render_lesson_prompt(title: str) -> str:
    return f"""# Инструкция для создания lesson.md

Прикрепи в чат файл `lesson-context.md`, затем отправь текст ниже.

```text
На основе приложенного контекста подготовь один самодостаточный Markdown-файл `lesson.md` для студента.

Тема лекции: «{title}».

Требования:
1. Пиши по-русски, но сохраняй важные термины на исходном языке и поясняй их.
2. Используй только факты из контекста. Не придумывай определения, примеры, формулы или выводы. Неразборчивые места кратко помечай как «не удалось подтвердить по записи».
3. Сделай ясную структуру: название, краткое резюме, цели обучения, основные разделы, ключевые понятия, связь со слайдами/демонстрацией, мини-словарь, вопросы для самопроверки и короткий план повторения.
4. Объединяй транскрипцию со слайдами и текстом экрана: слайды задают структуру, а речь добавляет объяснения и примеры.
5. Для ключевых утверждений указывай время из транскрипции в формате `[ЧЧ:ММ:СС]`, когда оно есть.
6. Используй Markdown с понятными заголовками, короткими абзацами, списками и таблицами только там, где они действительно упрощают учёбу.
7. Верни только содержимое готового `lesson.md`, без вступления о своей работе.
```
"""


def _normalise_text(value: str) -> str:
    return " ".join(value.split())


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}"
