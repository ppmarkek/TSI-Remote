"""Secure credential management using OS keyrings with legacy DPAPI migration."""

from __future__ import annotations

import base64
import ctypes
import sys
from pathlib import Path
from typing import Any

try:
    import keyring
    from keyring.backend import KeyringBackend
except ImportError:  # pragma: no cover
    keyring = None  # type: ignore[assignment]
    KeyringBackend = Any  # type: ignore[misc,assignment]

SERVICE_NAME = "Konspekt"


class SecretStoreError(RuntimeError):
    """The secure credential store could not complete an operation."""


class KeyringSecretStore:
    """Stores and retrieves secrets using the operating system's native keychain/keyring."""

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = backend

    def get_secret(self, service: str, account: str) -> str | None:
        try:
            if self._backend is not None:
                return self._backend.get_password(service, account)
            if keyring is not None:
                return keyring.get_password(service, account)
            return None
        except Exception:
            raise SecretStoreError("Не удалось прочитать ключ из системного хранилища.") from None

    def set_secret(self, service: str, account: str, secret: str) -> None:
        if not secret:
            self.delete_secret(service, account)
            return
        try:
            if self._backend is not None:
                self._backend.set_password(service, account, secret)
            elif keyring is not None:
                keyring.set_password(service, account, secret)
            else:
                raise SecretStoreError("Модуль keyring недоступен в окружении.")
        except SecretStoreError:
            raise
        except Exception:
            raise SecretStoreError("Не удалось сохранить ключ в системное хранилище.") from None

    def delete_secret(self, service: str, account: str) -> None:
        try:
            if self._backend is not None:
                self._backend.delete_password(service, account)
            elif keyring is not None:
                keyring.delete_password(service, account)
        except SecretStoreError:
            raise
        except Exception as exc:
            # Deletion is idempotent for a missing item, but a locked or
            # unavailable keychain must be reported to callers.
            missing_type = getattr(getattr(keyring, "errors", None), "PasswordDeleteError", None)
            if isinstance(missing_type, type) and isinstance(exc, missing_type):
                return
            raise SecretStoreError("Не удалось удалить ключ из системного хранилища.") from None


def migrate_legacy_windows_dpapi(
    legacy_file: Path,
    secret_store: KeyringSecretStore,
    service: str = SERVICE_NAME,
    account: str = "api_key",
) -> bool:
    """Migrate DPAPI-encrypted secrets on Windows into the KeyringSecretStore."""

    if not legacy_file.is_file():
        return False

    try:
        raw_text = legacy_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False

    if not raw_text:
        legacy_file.unlink(missing_ok=True)
        return False

    decrypted_key: str | None = None
    if sys.platform == "win32":
        try:
            decrypted_key = _decrypt_dpapi(raw_text)
        except Exception:
            decrypted_key = None

    if not decrypted_key:
        return False

    try:
        secret_store.set_secret(service, account, decrypted_key)
        verified = secret_store.get_secret(service, account)
        if verified == decrypted_key:
            legacy_file.unlink(missing_ok=True)
            return True
    except Exception:
        pass

    return False


def _decrypt_dpapi(ciphertext_b64: str) -> str:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.c_void_p)]

    raw_bytes = base64.b64decode(ciphertext_b64)
    blob_in = DATA_BLOB(
        len(raw_bytes), ctypes.cast(ctypes.create_string_buffer(raw_bytes), ctypes.c_void_p)
    )
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")

    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(blob_out.pbData)
