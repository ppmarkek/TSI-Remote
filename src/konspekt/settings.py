"""Persist application preferences without storing API keys as plain text."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .atomic_io import AtomicIOError, atomic_write_json
from .library_manager import default_library_path
from .platform_services import KeyringSecretStore, SecretStore, SecretStoreError

DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_CHATGPT_MODEL = "gpt-5.5"
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
SUPPORTED_PROVIDERS = ("openai", "deepseek", "claude", "gemini")
PROVIDER_LABELS = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "claude": "Claude",
    "gemini": "Gemini",
}
_PROVIDER_DEFAULT_MODELS = {
    "openai": DEFAULT_OPENAI_MODEL,
    "deepseek": DEFAULT_DEEPSEEK_MODEL,
    "claude": DEFAULT_CLAUDE_MODEL,
    "gemini": DEFAULT_GEMINI_MODEL,
}
_PROVIDER_KEY_VARIABLES = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
SUPPORTED_WHISPER_MODELS = ("tiny", "base", "small")
SUPPORTED_FRAME_INTERVALS = (30, 60, 90)
SERVICE_NAME = "Konspekt"
# Version 0.2 kept a single key here, for whichever provider was chosen then.
SECRET_ACCOUNT = "api_key"
SCHEMA_VERSION = 2


class SettingsError(RuntimeError):
    """Application settings could not be loaded or stored safely."""


@dataclass(frozen=True)
class AppSettings:
    """User-controlled processing and API preferences.

    ``api_models`` and ``api_keys`` keep a model and a key for every provider.
    ``api_provider`` is the default one, and ``api_model``/``api_key`` are its
    values: they always win over its entries in the two mappings. Switch the
    provider with :meth:`for_provider`, which carries the matching key along.
    """

    api_provider: str = "openai"
    api_model: str = DEFAULT_OPENAI_MODEL
    api_key: str = ""
    chatgpt_model: str = DEFAULT_CHATGPT_MODEL
    whisper_model: str = "base"
    frame_interval_seconds: int = 60
    ocr_enabled: bool = True
    api_models: Mapping[str, str] = field(default_factory=dict, hash=False)
    api_keys: Mapping[str, str] = field(default_factory=dict, hash=False, repr=False)
    # Providers whose key the keychain refused to give; saving leaves those keys alone.
    unread_keys: frozenset[str] = field(default=frozenset(), compare=False, repr=False)

    def __post_init__(self) -> None:
        models = {
            provider: str(self.api_models.get(provider, "")).strip()
            or default_model_for_provider(provider)
            for provider in SUPPORTED_PROVIDERS
        }
        keys = {provider: str(self.api_keys.get(provider, "")) for provider in SUPPORTED_PROVIDERS}
        if self.api_provider in models:
            models[self.api_provider] = self.api_model
            keys[self.api_provider] = self.api_key
        object.__setattr__(self, "api_models", models)
        object.__setattr__(self, "api_keys", keys)

    @property
    def api_configured(self) -> bool:
        return bool(self.api_key.strip() and self.api_model.strip())

    @property
    def provider_label(self) -> str:
        return PROVIDER_LABELS.get(self.api_provider, "OpenAI")

    def provider_configured(self, provider: str) -> bool:
        return bool(
            self.api_keys.get(provider, "").strip() and self.api_models.get(provider, "").strip()
        )

    @property
    def configured_providers(self) -> tuple[str, ...]:
        """Providers that have a key, the default one first."""

        others = (provider for provider in SUPPORTED_PROVIDERS if provider != self.api_provider)
        return tuple(
            provider
            for provider in (self.api_provider, *others)
            if self.provider_configured(provider)
        )

    def for_provider(self, provider: str) -> AppSettings:
        """These settings with ``provider`` as the API that requests go to."""

        if provider not in SUPPORTED_PROVIDERS:
            raise SettingsError("Выбран неизвестный API-провайдер.")
        return replace(
            self,
            api_provider=provider,
            api_model=self.api_models[provider],
            api_key=self.api_keys[provider],
        )

    def preferred_api(self) -> AppSettings:
        """The default provider, or the first one with a key when it has none."""

        configured = self.configured_providers
        if self.api_configured or not configured:
            return self
        return self.for_provider(configured[0])


def default_settings_path() -> Path:
    return default_library_path().parent / "settings.json"


def default_model_for_provider(provider: str) -> str:
    return _PROVIDER_DEFAULT_MODELS.get(provider, DEFAULT_OPENAI_MODEL)


def secret_account(provider: str) -> str:
    """The keyring account that holds the API key of ``provider``."""

    return f"{SECRET_ACCOUNT}.{provider}"


def load_settings(
    path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> AppSettings:
    """Load preferences and retrieve the API key from the secure store.

    Legacy DPAPI data is removed from ``settings.json`` only after the key was
    written to the target store and read back successfully.  A locked or
    unavailable keychain therefore cannot destroy the only durable copy.
    """

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

    stored_models = payload.get("api_models")
    models = {
        name: str(value).strip()
        for name, value in (stored_models.items() if isinstance(stored_models, dict) else ())
        if name in SUPPORTED_PROVIDERS
    }
    model = (
        str(payload.get("api_model", "")).strip()
        or models.get(provider, "")
        or default_model_for_provider(provider)
    )
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
    # A locked keychain is reported only to callers that passed their own store.
    strict = secret_store is not None
    keys = {name: "" for name in SUPPORTED_PROVIDERS}

    protected_key = str(payload.get("api_key_protected", "")).strip()
    if protected_key:
        try:
            decrypted = _unprotect_secret(protected_key)
        except SettingsError:
            decrypted = ""
        if decrypted:
            keys[provider] = decrypted
            if _store_and_verify_secret(store, secret_account(provider), decrypted):
                sanitized_payload = dict(payload)
                sanitized_payload.pop("api_key_protected", None)
                try:
                    atomic_write_json(
                        settings_path,
                        sanitized_payload,
                        ensure_ascii=False,
                        indent=2,
                    )
                except (AtomicIOError, OSError):
                    # The secure-store copy is already verified. Keeping the
                    # legacy field merely causes another idempotent migration.
                    pass

    unread: set[str] = set()
    for name in SUPPORTED_PROVIDERS:
        if not keys[name]:
            stored = _read_secret(store, secret_account(name))
            if stored is None:
                unread.add(name)
            keys[name] = stored or ""

    if not keys[provider]:
        legacy_key = _read_secret(store, SECRET_ACCOUNT)
        if legacy_key is None:
            unread.add(provider)
        elif legacy_key:
            keys[provider] = legacy_key
            unread.discard(provider)
            if _store_and_verify_secret(store, secret_account(provider), legacy_key):
                try:
                    store.delete_secret(SERVICE_NAME, SECRET_ACCOUNT)
                except SecretStoreError:
                    # The next save removes it; until then the copies agree.
                    pass
    if strict and provider in unread:
        raise SettingsError("Не удалось прочитать API-ключ из системного хранилища.")

    settings = AppSettings(
        api_provider=provider,
        api_model=model,
        api_key=keys[provider],
        chatgpt_model=chatgpt_model,
        whisper_model=whisper_model,
        frame_interval_seconds=frame_interval,
        ocr_enabled=bool(payload.get("ocr_enabled", True)),
        api_models=models,
        api_keys=keys,
        unread_keys=frozenset(unread),
    )
    return _with_environment_key(settings)


def save_settings(
    settings: AppSettings,
    path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> Path:
    """Atomically save preferences and store the API keys in the platform keyring."""

    validated = _validated(settings)
    settings_path = path or default_settings_path()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "api_provider": validated.api_provider,
        "api_model": validated.api_model,
        "api_models": dict(validated.api_models),
        "chatgpt_model": validated.chatgpt_model,
        "whisper_model": validated.whisper_model,
        "frame_interval_seconds": validated.frame_interval_seconds,
        "ocr_enabled": validated.ocr_enabled,
    }

    store = secret_store or KeyringSecretStore()
    try:
        for provider, api_key in validated.api_keys.items():
            from_environment = not api_key or api_key == _environment_key(provider)
            if from_environment and provider in validated.unread_keys:
                # Nothing typed in for a key that could not be read: keep what is stored.
                continue
            account = secret_account(provider)
            if (store.get_secret(SERVICE_NAME, account) or "") == api_key:
                continue
            if from_environment:
                # A key from an environment variable is read from there again.
                store.delete_secret(SERVICE_NAME, account)
                continue
            store.set_secret(SERVICE_NAME, account, api_key)
            if store.get_secret(SERVICE_NAME, account) != api_key:
                raise SecretStoreError("Системное хранилище не подтвердило запись ключа.")
        # The key of version 0.2 now lives in its provider's own account.
        if not validated.unread_keys:
            store.delete_secret(SERVICE_NAME, SECRET_ACCOUNT)
    except SecretStoreError as exc:
        raise SettingsError(
            "Не удалось сохранить API-ключ в системном хранилище. "
            "Разблокируй Keychain/Credential Manager и повтори попытку."
        ) from exc

    try:
        atomic_write_json(settings_path, payload, ensure_ascii=False, indent=2)
    except (AtomicIOError, OSError) as exc:
        raise SettingsError("Не удалось сохранить настройки приложения.") from exc
    return settings_path


def _store_and_verify_secret(store: SecretStore, account: str, secret: str) -> bool:
    try:
        store.set_secret(SERVICE_NAME, account, secret)
        return store.get_secret(SERVICE_NAME, account) == secret
    except SecretStoreError:
        return False


def _read_secret(store: SecretStore, account: str) -> str | None:
    """The stored secret, "" when there is none, None when the store failed."""

    try:
        return store.get_secret(SERVICE_NAME, account) or ""
    except SecretStoreError:
        return None


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
        api_models={name: value.strip() for name, value in settings.api_models.items()},
        api_keys={name: value.strip() for name, value in settings.api_keys.items()},
        unread_keys=settings.unread_keys,
    )


def _environment_key(provider: str) -> str:
    return os.environ.get(_PROVIDER_KEY_VARIABLES.get(provider, ""), "").strip()


def _with_environment_key(settings: AppSettings) -> AppSettings:
    keys = {
        provider: key or _environment_key(provider) for provider, key in settings.api_keys.items()
    }
    if keys == settings.api_keys:
        return settings
    return replace(
        settings,
        api_key=keys.get(settings.api_provider, settings.api_key),
        api_keys=keys,
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
