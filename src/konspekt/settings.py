"""Persist application preferences without storing API keys as plain text."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_json
from .bbb_import import default_library_path
from .platform_services import KeyringSecretStore, SecretStore, SecretStoreError

DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_CHATGPT_MODEL = "gpt-5.5"
SUPPORTED_PROVIDERS = ("openai", "deepseek")
SUPPORTED_WHISPER_MODELS = ("tiny", "base", "small")
SUPPORTED_FRAME_INTERVALS = (30, 60, 90)
SERVICE_NAME = "Konspekt"


class SettingsError(RuntimeError):
    """Application settings could not be loaded or stored safely."""


@dataclass(frozen=True)
class AppSettings:
    """User-controlled processing and API preferences."""

    api_provider: str = "openai"
    api_model: str = DEFAULT_OPENAI_MODEL
    api_key: str = ""
    chatgpt_model: str = DEFAULT_CHATGPT_MODEL
    whisper_model: str = "base"
    frame_interval_seconds: int = 60
    ocr_enabled: bool = True

    @property
    def api_configured(self) -> bool:
        return bool(self.api_key.strip() and self.api_model.strip())

    @property
    def provider_label(self) -> str:
        return "OpenAI" if self.api_provider == "openai" else "DeepSeek"


def default_settings_path() -> Path:
    return default_library_path().parent / "settings.json"


def default_model_for_provider(provider: str) -> str:
    return DEFAULT_DEEPSEEK_MODEL if provider == "deepseek" else DEFAULT_OPENAI_MODEL


def load_settings(
    path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> AppSettings:
    """Load settings and retrieve the API key from the keyring or legacy store."""

    settings_path = path or default_settings_path()
    if not settings_path.is_file():
        return _with_environment_key(AppSettings())

    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsError("Не удалось прочитать настройки приложения.") from exc
    if not isinstance(payload, dict):
        raise SettingsError("Файл настроек имеет неверный формат.")

    provider = str(payload.get("api_provider", "openai")).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = "openai"

    model = str(payload.get("api_model", "")).strip() or default_model_for_provider(provider)
    chatgpt_model = str(payload.get("chatgpt_model", "")).strip() or DEFAULT_CHATGPT_MODEL
    whisper_model = str(payload.get("whisper_model", "base")).strip().lower()
    if whisper_model not in SUPPORTED_WHISPER_MODELS:
        whisper_model = "base"

    try:
        frame_interval = int(payload.get("frame_interval_seconds", 60))
    except (TypeError, ValueError):
        frame_interval = 60
    if frame_interval not in SUPPORTED_FRAME_INTERVALS:
        frame_interval = 60

    store = secret_store or KeyringSecretStore()
    api_key = ""

    protected_key = str(payload.get("api_key_protected", "")).strip()
    if protected_key:
        try:
            decrypted = _unprotect_secret(protected_key)
            if decrypted:
                api_key = decrypted
        except SettingsError:
            api_key = ""

    if not api_key and secret_store is not None:
        try:
            retrieved = store.get_secret(SERVICE_NAME, "api_key")
            if retrieved:
                api_key = retrieved
        except SecretStoreError:
            pass

    if not api_key and not protected_key:
        try:
            retrieved = store.get_secret(SERVICE_NAME, "api_key")
            if retrieved:
                api_key = retrieved
        except SecretStoreError:
            pass

    settings = AppSettings(
        api_provider=provider,
        api_model=model,
        api_key=api_key,
        chatgpt_model=chatgpt_model,
        whisper_model=whisper_model,
        frame_interval_seconds=frame_interval,
        ocr_enabled=bool(payload.get("ocr_enabled", True)),
    )
    return _with_environment_key(settings)


def save_settings(
    settings: AppSettings,
    path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> Path:
    """Atomically save preferences and store the API key in the platform SecretStore."""

    validated = _validated(settings)
    settings_path = path or default_settings_path()
    payload: dict[str, Any] = asdict(validated)
    api_key = str(payload.pop("api_key", "")).strip()
    payload["schema_version"] = 1

    store = secret_store or KeyringSecretStore()
    try:
        if api_key:
            store.set_secret(SERVICE_NAME, "api_key", api_key)
        else:
            store.delete_secret(SERVICE_NAME, "api_key")
    except SecretStoreError:
        pass

    try:
        payload["api_key_protected"] = _protect_secret(api_key) if api_key else ""
    except SettingsError:
        payload["api_key_protected"] = ""

    try:
        atomic_write_json(settings_path, payload, ensure_ascii=False, indent=2)
    except (AtomicIOError, OSError) as exc:
        raise SettingsError("Не удалось сохранить настройки приложения.") from exc
    return settings_path


def _validated(settings: AppSettings) -> AppSettings:
    provider = settings.api_provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise SettingsError("Выбран неизвестный API-провайдер.")
    model = settings.api_model.strip()
    if not model:
        raise SettingsError("Укажи модель API.")
    chatgpt_model = settings.chatgpt_model.strip()
    if not chatgpt_model:
        raise SettingsError("Выбери модель личного ChatGPT.")
    whisper_model = settings.whisper_model.strip().lower()
    if whisper_model not in SUPPORTED_WHISPER_MODELS:
        raise SettingsError("Выбрана неизвестная модель распознавания речи.")
    if settings.frame_interval_seconds not in SUPPORTED_FRAME_INTERVALS:
        raise SettingsError("Выбран неподдерживаемый интервал кадров.")
    return AppSettings(
        api_provider=provider,
        api_model=model,
        api_key=settings.api_key.strip(),
        chatgpt_model=chatgpt_model,
        whisper_model=whisper_model,
        frame_interval_seconds=settings.frame_interval_seconds,
        ocr_enabled=bool(settings.ocr_enabled),
    )


def _with_environment_key(settings: AppSettings) -> AppSettings:
    if settings.api_key:
        return settings
    variable = "OPENAI_API_KEY" if settings.api_provider == "openai" else "DEEPSEEK_API_KEY"
    environment_key = os.environ.get(variable, "").strip()
    if not environment_key:
        return settings
    return AppSettings(
        api_provider=settings.api_provider,
        api_model=settings.api_model,
        api_key=environment_key,
        chatgpt_model=settings.chatgpt_model,
        whisper_model=settings.whisper_model,
        frame_interval_seconds=settings.frame_interval_seconds,
        ocr_enabled=settings.ocr_enabled,
    )


def _protect_secret(secret: str) -> str:
    if not secret:
        return ""
    encrypted = _crypt_protect(secret.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def _unprotect_secret(protected: str) -> str:
    try:
        encrypted = base64.b64decode(protected.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise SettingsError("Сохранённый API-ключ повреждён.") from exc
    try:
        return _crypt_unprotect(encrypted).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SettingsError("Сохранённый API-ключ повреждён.") from exc


def _crypt_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SettingsError(
            "Безопасное сохранение API-ключа через DPAPI доступно только в Windows."
        )
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32, kernel32 = _windows_crypto()
    buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    result = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Konspekt API key",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    if not result:
        raise SettingsError("Windows не смог защитить API-ключ.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _crypt_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SettingsError("Безопасное чтение API-ключа через DPAPI доступно только в Windows.")
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    crypt32, kernel32 = _windows_crypto()
    buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _DataBlob()
    result = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    if not result:
        raise SettingsError("Windows не смог расшифровать сохранённый API-ключ.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _windows_crypto() -> tuple[Any, Any]:
    if os.name != "nt":
        raise SettingsError("Безопасное сохранение API-ключа доступно только в Windows.")
    from ctypes import wintypes

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32
