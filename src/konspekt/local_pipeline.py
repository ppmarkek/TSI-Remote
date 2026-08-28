"""Local, no-token preparation of an imported BBB lecture for later summarisation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from .bbb_download import resolve_ffmpeg
from .bbb_import import (
    BBBImportError,
    BBBRecording,
    default_library_path,
    load_library,
    recording_identity,
)
from .lecture_manifest import ALGORITHM_VERSION, LectureManifest, file_sha256


class LocalProcessingError(RuntimeError):
    """The local lecture pipeline cannot continue until a local dependency is ready."""


ProgressCallback = Callable[[int, str], None]
DownloadProgressCallback = Callable[[str], None]
MediaDownloader = Callable[[str, Path, DownloadProgressCallback | None], None]


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class ScreenNote:
    timestamp_seconds: float
    image_path: str
    text: str


@dataclass(frozen=True)
class PreparedLecture:
    directory: Path
    audio_path: Path
    transcript_path: Path
    screen_notes_path: Path | None
    frame_count: int


class LocalTranscriber(Protocol):
    def __call__(
        self,
        audio_path: Path,
        language: str | None,
    ) -> Iterable[TranscriptSegment]: ...


def default_lecture_directory(
    recording: BBBRecording,
    base_dir: Path | None = None,
) -> Path:
    base = (
        (base_dir / "lectures")
        if base_dir is not None
        else (default_library_path().parent / "lectures")
    )
    meeting_id = recording.meeting_id.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,200}", meeting_id):
        directory_name = meeting_id
        lib_path = (
            (base_dir / "library.json")
            if (base_dir is not None and (base_dir / "library.json").is_file())
            else None
        )
        try:
            same_id = [
                item
                for item in load_library(path=lib_path)
                if item.meeting_id == recording.meeting_id
            ]
        except BBBImportError:
            same_id = []
        identities = {recording_identity(item) for item in same_id}
        if len(identities) > 1:
            # Once an id collision exists, every BBB origin gets a deterministic
            # directory. No mutable import timestamp can make two lectures swap
            # ownership of an existing cache on a later re-import.
            digest = hashlib.sha256(
                repr(recording_identity(recording)).encode("utf-8")
            ).hexdigest()[:12]
            directory_name = f"{meeting_id[:80]}-{digest}"
    else:
        digest = hashlib.sha256(repr(recording_identity(recording)).encode("utf-8")).hexdigest()[
            :24
        ]
        directory_name = f"lecture-{digest}"
    target = base / directory_name
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise LocalProcessingError("Небезопасный идентификатор лекции.") from exc
    return target


def lecture_is_prepared(recording: BBBRecording) -> bool:
    directory = default_lecture_directory(recording)
    return (directory / "transcript.md").is_file() and (directory / "transcript.json").is_file()


def prepare_lecture(
    recording: BBBRecording,
    *,
    directory: Path | None = None,
    model_name: str = "base",
    language: str | None = None,
    frame_interval_seconds: int = 60,
    enable_ocr: bool = True,
    progress: ProgressCallback | None = None,
    downloader: MediaDownloader | None = None,
    transcriber: LocalTranscriber | None = None,
    ocr_reader: Callable[[Path], str | None] | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> PreparedLecture:
    """Download only the required media and build local study material artefacts.

    A Whisper model is loaded locally. It may download its open model weights on
    first use, but no lecture text, frames, or audio are sent to a paid API.
    """

    if frame_interval_seconds <= 0:
        raise ValueError("frame_interval_seconds must be positive")

    target = directory or default_lecture_directory(recording)
    target.mkdir(parents=True, exist_ok=True)
    download = downloader or download_media
    notify = progress or _do_nothing
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise LocalProcessingError(
            "FFmpeg не найден. Запусти .\\setup_local_ai.ps1, затем повтори подготовку."
        )

    notify(4, "Проверяем локальные инструменты…")
    notify(10, "Скачиваем дорожку с голосом преподавателя…")
    webcam_path = target / f"webcams{_extension_from_url(recording.audio_video_url)}"
    if not webcam_path.is_file():
        download(
            recording.audio_video_url,
            webcam_path,
            lambda message: notify(22, message),
        )
    notify(24, "Дорожка с голосом готова.")

    audio_path = target / "audio.wav"
    if not audio_path.is_file():
        notify(30, "Извлекаем аудио локально…")
        _extract_audio(ffmpeg, webcam_path, audio_path, command_runner)
    notify(36, "Аудио подготовлено.")

    manifest = LectureManifest.for_recording(
        recording.title,
        recording.meeting_id,
        recording.source_url,
        target,
    )
    manifest_path = target / "lecture-manifest.json"

    transcript_path = target / "transcript.md"
    transcript_json_path = target / "transcript.json"
    transcription_fp = {
        "whisper_model": model_name,
        "language": language or "auto",
        "algorithm_version": ALGORITHM_VERSION,
    }

    can_reuse_transcript = False
    if transcript_path.is_file() and transcript_json_path.is_file():
        if manifest_path.is_file():
            can_reuse_transcript = manifest.is_stage_valid(
                "transcription", transcription_fp, target
            )
        else:
            can_reuse_transcript = True

    if can_reuse_transcript:
        notify(66, "Используем уже готовую локальную транскрипцию.")
    else:
        notify(40, "Распознаём речь локальной моделью Whisper…")
        recognise = transcriber or faster_whisper_transcribe(
            model_name,
            progress=notify,
        )
        try:
            segments = tuple(recognise(audio_path, language))
        except LocalProcessingError:
            raise
        except subprocess.TimeoutExpired as exc:
            raise LocalProcessingError(
                "Распознавание речи не завершилось вовремя. Повтори попытку — уже скачанные файлы сохранятся."
            ) from exc
        except Exception as exc:
            raise LocalProcessingError(
                "Whisper не смог обработать аудио. Проверь установку модели и свободное место на диске."
            ) from exc
        notify(62, "Сохраняем транскрипцию…")
        _write_transcript(target, segments)
        manifest.record_stage_success(
            "transcription",
            fingerprint=transcription_fp,
            outputs={
                "transcript.md": file_sha256(transcript_path),
                "transcript.json": file_sha256(transcript_json_path),
            },
        )
        try:
            manifest.save(manifest_path)
        except Exception:
            pass
        notify(66, "Транскрипция готова.")

    screen_notes_path: Path | None = None
    frame_count = 0
    ocr_fp = {
        "frame_interval_seconds": frame_interval_seconds,
        "enable_ocr": bool(enable_ocr),
        "algorithm_version": ALGORITHM_VERSION,
    }

    if recording.screen_video_url and not enable_ocr:
        existing_screen_notes = target / "screen-notes.json"
        if _screen_notes_cache_is_valid(existing_screen_notes):
            screen_notes_path = existing_screen_notes
            notify(96, "Используем уже готовые заметки с экрана.")
        else:
            notify(
                96,
                "Обработка демонстрации экрана отключена в настройках.",
            )
    elif recording.screen_video_url:
        notify(70, "Скачиваем демонстрацию экрана…")
        screen_path = target / f"deskshare{_extension_from_url(recording.screen_video_url)}"
        if not screen_path.is_file():
            download(
                recording.screen_video_url,
                screen_path,
                lambda message: notify(76, message),
            )

        frames_dir = target / "frames"
        frame_interval_path = frames_dir / "interval-seconds.txt"
        effective_frame_interval = frame_interval_seconds
        if not any(frames_dir.glob("frame-*.jpg")):
            notify(
                80,
                f"Выбираем кадры экрана каждые {frame_interval_seconds} секунд…",
            )
            _extract_frames(
                ffmpeg,
                screen_path,
                frames_dir,
                frame_interval_seconds,
                command_runner,
            )
            frame_interval_path.write_text(
                str(frame_interval_seconds),
                encoding="ascii",
            )
        frames = sorted(frames_dir.glob("frame-*.jpg"))
        frame_count = len(frames)
        if frame_interval_path.is_file():
            try:
                saved_interval = int(frame_interval_path.read_text(encoding="ascii").strip())
                if saved_interval > 0:
                    effective_frame_interval = saved_interval
            except (OSError, UnicodeError, ValueError):
                pass
        elif frames:
            # Builds before interval metadata used the historical 30-second default.
            effective_frame_interval = 30

        existing_screen_notes = target / "screen-notes.json"
        can_reuse_ocr = False
        if _screen_notes_cache_is_valid(existing_screen_notes):
            if manifest_path.is_file():
                can_reuse_ocr = manifest.is_stage_valid("ocr", ocr_fp, target)
            else:
                can_reuse_ocr = True

        if can_reuse_ocr:
            screen_notes_path = existing_screen_notes
            notify(96, "Используем уже готовые заметки с экрана.")
        else:
            if ocr_reader is None:
                ocr_reader = default_ocr_reader()
        if enable_ocr and screen_notes_path is None and ocr_reader is not None:
            notify(88, "Распознаём текст на экране локально…")
            notes = _read_frames(
                frames,
                effective_frame_interval,
                ocr_reader,
                progress=notify,
            )
            screen_notes_path = target / "screen-notes.json"
            notify(96, "Сохраняем заметки с экрана…")
            temporary_notes_path = screen_notes_path.with_suffix(".json.tmp")
            temporary_notes_path.write_text(
                json.dumps([asdict(note) for note in notes], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_notes_path.replace(screen_notes_path)
            manifest.record_stage_success(
                "ocr",
                fingerprint=ocr_fp,
                outputs={"screen-notes.json": file_sha256(screen_notes_path)},
            )
            try:
                manifest.save(manifest_path)
            except Exception:
                pass
        elif enable_ocr and screen_notes_path is None:
            notify(96, "Кадры сохранены. OCR пропущен: Tesseract пока не установлен.")

    notify(100, "Материалы готовы для следующего шага.")
    return PreparedLecture(
        directory=target,
        audio_path=audio_path,
        transcript_path=transcript_path,
        screen_notes_path=screen_notes_path,
        frame_count=frame_count,
    )


def download_media(
    url: str,
    destination: Path,
    progress: DownloadProgressCallback | None = None,
) -> None:
    """Download one public BBB asset with bounded, local file handling."""
    from .download_manager import DownloadError, download_file_resumable

    def on_progress(percent: int, msg: str) -> None:
        if progress:
            progress(msg)

    try:
        download_file_resumable(
            url,
            destination,
            progress=on_progress,
        )
    except DownloadError as exc:
        raise LocalProcessingError(
            "Не удалось скачать один из файлов BBB-записи. Проверь подключение и повтори попытку."
        ) from exc
    except OSError as exc:
        raise LocalProcessingError(
            "Не удалось сохранить скачанные материалы лекции на диск. Проверь права и свободное место."
        ) from exc


def faster_whisper_transcribe(
    model_name: str,
    model_factory: Callable[..., object] | None = None,
    progress: ProgressCallback | None = None,
) -> LocalTranscriber:
    """Build a CPU-friendly Faster-Whisper transcriber only when it is needed."""

    if model_factory is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise LocalProcessingError(
                "Локальный Whisper не установлен. Запусти .\\setup_local_ai.ps1, затем повтори подготовку."
            ) from exc
        model_factory = WhisperModel

    notify = progress or _do_nothing
    notify(41, f"Загружаем локальную модель Whisper {model_name}…")
    try:
        model = model_factory(model_name, device="cpu", compute_type="int8")
    except LocalProcessingError:
        raise
    except Exception as exc:
        raise LocalProcessingError(
            "Не удалось загрузить локальную модель Whisper. Проверь интернет для первой загрузки модели и свободное место на диске."
        ) from exc
    notify(43, "Модель Whisper готова. Начинаем распознавание речи…")

    def transcribe(audio_path: Path, language: str | None) -> Iterable[TranscriptSegment]:
        try:
            raw_segments, info = model.transcribe(
                str(audio_path),
                language=language,
                vad_filter=True,
            )
            duration = float(getattr(info, "duration", 0) or 0)
            converted: list[TranscriptSegment] = []
            last_percent = 43
            for segment_index, segment in enumerate(raw_segments, start=1):
                end_seconds = float(segment.end)
                if duration > 0:
                    ratio = min(max(end_seconds / duration, 0), 1)
                    current_percent = 44 + int(ratio * 17)
                else:
                    current_percent = 44
                if current_percent > last_percent or segment_index % 25 == 0:
                    reported_percent = max(last_percent, current_percent)
                    notify(
                        reported_percent,
                        f"Распознаём речь: обработано до {_format_timestamp(end_seconds)}…",
                    )
                    last_percent = reported_percent

                text = segment.text.strip()
                if text:
                    converted.append(
                        TranscriptSegment(
                            start_seconds=float(segment.start),
                            end_seconds=end_seconds,
                            text=text,
                        )
                    )
            notify(61, f"Речь распознана: фрагментов {len(converted)}.")
            return tuple(converted)
        except LocalProcessingError:
            raise
        except subprocess.TimeoutExpired as exc:
            raise LocalProcessingError(
                "Распознавание речи не завершилось вовремя. Повтори попытку позже."
            ) from exc
        except Exception as exc:
            raise LocalProcessingError(
                "Whisper не смог обработать аудио. Проверь установку модели и свободное место на диске."
            ) from exc

    return transcribe


def default_ocr_reader() -> Callable[[Path], str | None] | None:
    executable = _find_tesseract_executable()
    if not executable:
        return None

    def read(image_path: Path) -> str | None:
        preferred = [executable, str(image_path), "stdout", "-l", "eng+rus"]
        try:
            result = subprocess.run(
                preferred,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=_tesseract_environment(executable),
                timeout=45,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    [executable, str(image_path), "stdout"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    env=_tesseract_environment(executable),
                    timeout=45,
                )
            if result.returncode != 0:
                raise LocalProcessingError(
                    f"Tesseract не смог распознать кадр {image_path.name}. "
                    "Кадр останется доступен для повторной попытки."
                )
        except subprocess.TimeoutExpired as exc:
            raise LocalProcessingError(
                f"Tesseract слишком долго обрабатывал кадр {image_path.name}."
            ) from exc
        except OSError as exc:
            raise LocalProcessingError(
                "Не удалось запустить локальный Tesseract для OCR экрана."
            ) from exc
        return (result.stdout or "").strip() or None

    return read


def _find_tesseract_executable() -> str | None:
    candidates = [shutil.which("tesseract")]
    if os.name == "nt":
        candidates.append(
            str(
                Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                / "Tesseract-OCR"
                / "tesseract.exe"
            )
        )
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidates.append(str(bundle_root / "tesseract" / "tesseract.exe"))

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _tesseract_environment(executable: str) -> dict[str, str]:
    environment = os.environ.copy()
    tessdata = Path(executable).parent / "tessdata"
    if tessdata.is_dir():
        environment.setdefault("TESSDATA_PREFIX", str(tessdata))
    return environment


def _extract_audio(
    ffmpeg: str,
    source: Path,
    destination: Path,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    temporary = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    temporary.unlink(missing_ok=True)
    try:
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-nostdin",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ],
            command_runner,
        )
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise LocalProcessingError("FFmpeg не создал аудиофайл для распознавания.")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _extract_frames(
    ffmpeg: str,
    source: Path,
    frames_dir: Path,
    interval_seconds: int,
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    temporary_dir = frames_dir.with_name(f"{frames_dir.name}.part")
    if temporary_dir.exists():
        shutil.rmtree(temporary_dir, ignore_errors=True)
    temporary_dir.mkdir(parents=True, exist_ok=True)
    try:
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-nostdin",
                "-i",
                str(source),
                "-vf",
                f"fps=1/{interval_seconds}",
                "-q:v",
                "3",
                str(temporary_dir / "frame-%04d.jpg"),
            ],
            command_runner,
        )
        if not any(temporary_dir.glob("frame-*.jpg")):
            raise LocalProcessingError("FFmpeg не создал кадры демонстрации экрана.")
        (temporary_dir / "interval-seconds.txt").write_text(
            str(interval_seconds),
            encoding="ascii",
        )
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        temporary_dir.replace(frames_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def _run_ffmpeg(
    command: list[str],
    command_runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    try:
        result = command_runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30 * 60,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalProcessingError(
            "FFmpeg не завершил обработку за 30 минут. Проверь файл записи и повтори попытку."
        ) from exc
    except OSError as exc:
        raise LocalProcessingError(
            "Не удалось запустить FFmpeg. Повтори локальную установку инструментов."
        ) from exc
    if result.returncode != 0:
        raise LocalProcessingError(
            "FFmpeg не смог подготовить файл. Проверь доступность записи и свободное место на диске."
        )


def _write_transcript(directory: Path, segments: tuple[TranscriptSegment, ...]) -> None:
    payload = [asdict(segment) for segment in segments]
    (directory / "transcript.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = ["# Транскрипция", ""]
    for segment in segments:
        markdown.append(f"- **{_format_timestamp(segment.start_seconds)}** — {segment.text}")
    (directory / "transcript.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def _read_frames(
    frames: list[Path],
    interval_seconds: int,
    reader: Callable[[Path], str | None],
    *,
    progress: ProgressCallback | None = None,
) -> tuple[ScreenNote, ...]:
    notes: list[ScreenNote] = []
    notify = progress or _do_nothing
    failure_count = 0
    frame_count = len(frames)
    for index, frame in enumerate(frames):
        try:
            text = reader(frame)
        except Exception:
            failure_count += 1
            text = None
        if text:
            notes.append(
                ScreenNote(
                    timestamp_seconds=index * interval_seconds,
                    image_path=str(frame),
                    text=text,
                )
            )
        completed = index + 1
        percent = 88 + (int(completed * 7 / frame_count) if frame_count else 7)
        notify(
            min(percent, 95),
            f"OCR экрана: обработано кадров {completed} из {frame_count}…",
        )
    if failure_count:
        notify(
            95,
            f"OCR завершён: кадров {frame_count}, пропущено из-за ошибок {failure_count}.",
        )
    if frame_count and failure_count == frame_count:
        raise LocalProcessingError(
            "Tesseract не смог обработать ни одного кадра. Повтори OCR: транскрипция и кадры уже сохранены."
        )
    return tuple(notes)


def _screen_notes_cache_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, list)


def _extension_from_url(url: str) -> str:
    extension = Path(urlparse(url).path).suffix
    return extension if extension else ".webm"


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}"


def _do_nothing(_: int, __: str) -> None:
    pass
