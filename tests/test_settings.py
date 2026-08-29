from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from konspekt.settings import (
    DEFAULT_CHATGPT_MODEL,
    AppSettings,
    SettingsError,
    load_settings,
    save_settings,
)


class MockSecretStore:
    def __init__(self) -> None:
        self._secrets: dict[tuple[str, str], str] = {}

    def get_secret(self, service: str, account: str) -> str | None:
        return self._secrets.get((service, account))

    def set_secret(self, service: str, account: str, secret: str) -> None:
        if not secret:
            self.delete_secret(service, account)
            return
        self._secrets[(service, account)] = secret

    def delete_secret(self, service: str, account: str) -> None:
        self._secrets.pop((service, account), None)


class SettingsTests(unittest.TestCase):
    def test_round_trips_api_settings_without_writing_secret_to_json(self) -> None:
        settings = AppSettings(
            api_provider="openai",
            api_model="gpt-5.6-luna",
            api_key="sk-test-secret-must-not-be-in-json",
            chatgpt_model="gpt-5.5",
            whisper_model="tiny",
            frame_interval_seconds=90,
            ocr_enabled=False,
        )

        store = MockSecretStore()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(settings, path=path, secret_store=store)

            raw_json = path.read_text(encoding="utf-8")
            payload = json.loads(raw_json)
            loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(payload["api_provider"], "openai")
        self.assertEqual(payload["api_model"], "gpt-5.6-luna")
        self.assertEqual(payload["chatgpt_model"], "gpt-5.5")
        self.assertNotIn("api_key_protected", payload)
        self.assertNotIn(settings.api_key, raw_json)
        self.assertNotIn("api_key", payload)
        self.assertEqual(loaded, settings)

    def test_legacy_dpapi_migrates_to_keyring_and_strips_from_file(self) -> None:
        payload = {
            "schema_version": 1,
            "api_provider": "openai",
            "api_model": "gpt-5.6-luna",
            "api_key_protected": "legacy-protected-data",
            "chatgpt_model": "gpt-5.5",
            "whisper_model": "base",
            "frame_interval_seconds": 60,
            "ocr_enabled": True,
        }
        store = MockSecretStore()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("konspekt.settings._unprotect_secret", return_value="decrypted-legacy-key"):
                loaded = load_settings(path=path, secret_store=store)

            self.assertEqual(loaded.api_key, "decrypted-legacy-key")
            # Verify migrated into secret store
            self.assertEqual(store.get_secret("Konspekt", "api_key"), "decrypted-legacy-key")
            # Verify stripped from settings.json
            rewritten = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("api_key_protected", rewritten)

    def test_old_settings_default_to_primary_chatgpt_model(self) -> None:
        payload = {
            "schema_version": 1,
            "api_provider": "deepseek",
            "api_model": "deepseek-v4-flash",
            "api_key_protected": "",
            "whisper_model": "base",
            "frame_interval_seconds": 60,
            "ocr_enabled": True,
        }

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_settings(path=path)

        self.assertEqual(loaded.chatgpt_model, DEFAULT_CHATGPT_MODEL)

    def test_rejects_empty_chatgpt_model_when_saving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            with self.assertRaises(SettingsError):
                save_settings(AppSettings(chatgpt_model="   "), path=path)


if __name__ == "__main__":
    unittest.main()
