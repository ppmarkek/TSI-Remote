from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.platform_services import (
    KeyringSecretStore,
    SecretStoreError,
    migrate_legacy_windows_dpapi,
)


class FakeKeyringBackend:
    def __init__(self, should_fail: bool = False) -> None:
        self.storage: dict[tuple[str, str], str] = {}
        self.should_fail = should_fail

    def get_password(self, service: str, account: str) -> str | None:
        if self.should_fail:
            raise RuntimeError("Keyring locked by OS")
        return self.storage.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        if self.should_fail:
            raise RuntimeError("Keyring write denied")
        self.storage[(service, account)] = password

    def delete_password(self, service: str, account: str) -> None:
        if self.should_fail:
            raise RuntimeError("Keyring delete denied")
        self.storage.pop((service, account), None)


class SecretStoreTests(unittest.TestCase):
    def test_round_trip_with_backend(self) -> None:
        backend = FakeKeyringBackend()
        store = KeyringSecretStore(backend=backend)  # type: ignore[arg-type]

        self.assertIsNone(store.get_secret("Konspekt", "test_key"))

        store.set_secret("Konspekt", "test_key", "sk-secret-token-12345")
        self.assertEqual(store.get_secret("Konspekt", "test_key"), "sk-secret-token-12345")

        store.delete_secret("Konspekt", "test_key")
        self.assertIsNone(store.get_secret("Konspekt", "test_key"))

    def test_locked_backend_raises_domain_error_without_secret_leak(self) -> None:
        backend = FakeKeyringBackend(should_fail=True)
        store = KeyringSecretStore(backend=backend)  # type: ignore[arg-type]

        super_secret = "sk-super-confidential-key"
        with self.assertRaises(SecretStoreError) as exc_info:
            store.set_secret("Konspekt", "api_key", super_secret)

        self.assertNotIn(super_secret, str(exc_info.exception))

        with self.assertRaises(SecretStoreError) as exc_info:
            store.get_secret("Konspekt", "api_key")

        self.assertNotIn(super_secret, str(exc_info.exception))

    def test_legacy_dpapi_migration(self) -> None:
        backend = FakeKeyringBackend()
        store = KeyringSecretStore(backend=backend)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_file = Path(temp_dir) / "api_key.dat"
            legacy_file.write_text("base64ciphertext", encoding="utf-8")

            with patch(
                "konspekt.platform_services.secrets._decrypt_dpapi", return_value="sk-migrated-key"
            ):
                with patch("sys.platform", "win32"):
                    migrated = migrate_legacy_windows_dpapi(legacy_file, store)

            self.assertTrue(migrated)
            self.assertEqual(store.get_secret("Konspekt", "api_key"), "sk-migrated-key")
            self.assertFalse(legacy_file.exists())

    def test_legacy_dpapi_migration_handles_missing_or_empty_file(self) -> None:
        backend = FakeKeyringBackend()
        store = KeyringSecretStore(backend=backend)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_file = Path(temp_dir) / "nonexistent.dat"
            self.assertFalse(migrate_legacy_windows_dpapi(missing_file, store))

            empty_file = Path(temp_dir) / "empty.dat"
            empty_file.write_text("  ", encoding="utf-8")
            self.assertFalse(migrate_legacy_windows_dpapi(empty_file, store))
            self.assertFalse(empty_file.exists())


if __name__ == "__main__":
    unittest.main()
