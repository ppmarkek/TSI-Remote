from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from konspekt.platform_services import SecretStoreError
from konspekt.settings import (
    DEFAULT_CHATGPT_MODEL,
    AppSettings,
    SettingsError,
    default_model_for_provider,
    load_settings,
    save_settings,
)

KEY_VARIABLES = ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")


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


class FlakySecretStore(MockSecretStore):
    """Refuses to read the accounts in ``failing``."""

    def __init__(self) -> None:
        super().__init__()
        self.failing: set[str] = set()

    def get_secret(self, service: str, account: str) -> str | None:
        if account in self.failing:
            raise SecretStoreError("locked")
        return super().get_secret(service, account)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keys from the developer's own environment must not leak into these tests.
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for variable in KEY_VARIABLES:
            os.environ.pop(variable, None)

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
            self.assertEqual(store.get_secret("Konspekt", "api_key.openai"), "decrypted-legacy-key")
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

    def test_claude_provider_round_trips_with_its_default_model(self) -> None:
        store = MockSecretStore()
        settings = AppSettings(
            api_provider="claude",
            api_model=default_model_for_provider("claude"),
            api_key="sk-ant-test",
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(settings, path=path, secret_store=store)
            loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded, settings)
        self.assertEqual(loaded.api_model, "claude-opus-5-5")
        self.assertEqual(loaded.provider_label, "Claude")
        self.assertTrue(loaded.api_configured)

    def test_claude_key_can_come_from_the_environment(self) -> None:
        store = MockSecretStore()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(
                AppSettings(api_provider="claude", api_model="claude-opus-5-5"),
                path=path,
                secret_store=store,
            )
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-env"}):
                loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded.api_key, "sk-ant-env")

    def test_every_provider_keeps_its_own_key_and_model(self) -> None:
        store = MockSecretStore()
        settings = AppSettings(
            api_provider="openai",
            api_model="gpt-5.6-luna",
            api_key="sk-openai",
            api_models={"gemini": "gemini-4-argon"},
            api_keys={"claude": "sk-ant-test", "gemini": "AIza-test"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(settings, path=path, secret_store=store)
            raw_json = path.read_text(encoding="utf-8")
            loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded, settings)
        self.assertNotIn("sk-ant-test", raw_json)
        self.assertNotIn("AIza-test", raw_json)
        self.assertEqual(store.get_secret("Konspekt", "api_key.claude"), "sk-ant-test")
        self.assertEqual(store.get_secret("Konspekt", "api_key.gemini"), "AIza-test")
        self.assertIsNone(store.get_secret("Konspekt", "api_key.deepseek"))
        self.assertEqual(loaded.configured_providers, ("openai", "claude", "gemini"))

        gemini = loaded.for_provider("gemini")
        self.assertEqual((gemini.api_model, gemini.api_key), ("gemini-4-argon", "AIza-test"))
        self.assertEqual(gemini.provider_label, "Gemini")
        claude = loaded.for_provider("claude")
        self.assertEqual((claude.api_model, claude.api_key), ("claude-opus-5-5", "sk-ant-test"))
        with self.assertRaises(SettingsError):
            loaded.for_provider("mistral")

    def test_removing_a_key_deletes_only_that_account(self) -> None:
        store = MockSecretStore()
        keys = {"claude": "sk-ant-test", "gemini": "AIza-test"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(AppSettings(api_key="sk-openai", api_keys=keys), path, store)
            keys["claude"] = ""
            save_settings(AppSettings(api_key="sk-openai", api_keys=keys), path, store)
            loaded = load_settings(path=path, secret_store=store)

        self.assertIsNone(store.get_secret("Konspekt", "api_key.claude"))
        self.assertEqual(loaded.configured_providers, ("openai", "gemini"))

    def test_single_key_of_version_0_2_moves_to_its_provider(self) -> None:
        store = MockSecretStore()
        store.set_secret("Konspekt", "api_key", "sk-deepseek-old")
        payload = {
            "schema_version": 1,
            "api_provider": "deepseek",
            "api_model": "deepseek-v4-flash",
            "chatgpt_model": "gpt-5.5",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded.api_key, "sk-deepseek-old")
        self.assertEqual(loaded.api_keys["deepseek"], "sk-deepseek-old")
        self.assertEqual(loaded.api_models["openai"], default_model_for_provider("openai"))
        self.assertEqual(store.get_secret("Konspekt", "api_key.deepseek"), "sk-deepseek-old")
        self.assertIsNone(store.get_secret("Konspekt", "api_key"))

    def test_preferred_api_falls_back_to_a_provider_with_a_key(self) -> None:
        settings = AppSettings(api_provider="openai", api_keys={"gemini": "AIza-test"})

        preferred = settings.preferred_api()

        self.assertFalse(settings.api_configured)
        self.assertEqual(preferred.api_provider, "gemini")
        self.assertEqual(preferred.api_model, default_model_for_provider("gemini"))
        self.assertTrue(preferred.api_configured)
        nothing = AppSettings()
        self.assertIs(nothing.preferred_api(), nothing)
        self.assertEqual(nothing.configured_providers, ())

    def test_gemini_defaults_and_environment_key(self) -> None:
        self.assertEqual(default_model_for_provider("gemini"), "gemini-3.8-flash")
        store = MockSecretStore()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(
                AppSettings(api_provider="gemini", api_model="gemini-3.8-flash"),
                path=path,
                secret_store=store,
            )
            with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-env"}):
                loaded = load_settings(path=path, secret_store=store)
                # A key from the environment is not copied into the keychain.
                save_settings(loaded, path=path, secret_store=store)

        self.assertEqual(
            (loaded.api_provider, loaded.api_model, loaded.api_key),
            ("gemini", "gemini-3.8-flash", "AIza-env"),
        )
        self.assertIsNone(store.get_secret("Konspekt", "api_key.gemini"))

    def test_unreadable_keys_survive_a_save(self) -> None:
        store = FlakySecretStore()
        settings = AppSettings(api_key="sk-openai", api_keys={"claude": "sk-ant-test"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(settings, path=path, secret_store=store)
            store.set_secret("Konspekt", "api_key", "sk-legacy")
            store.failing = {"api_key.claude"}
            loaded = load_settings(path=path, secret_store=store)
            store.failing = set()
            save_settings(replace(loaded, api_model="gpt-5.5"), path=path, secret_store=store)
            reloaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded.api_keys["claude"], "")
        self.assertEqual(loaded.unread_keys, frozenset({"claude"}))
        self.assertEqual(store.get_secret("Konspekt", "api_key.claude"), "sk-ant-test")
        # The 0.2 account is only cleaned up once every key could be read.
        self.assertEqual(store.get_secret("Konspekt", "api_key"), "sk-legacy")
        self.assertEqual(reloaded.api_keys["claude"], "sk-ant-test")
        self.assertEqual(reloaded.api_model, "gpt-5.5")

    def test_unreadable_default_key_fails_only_for_an_explicit_store(self) -> None:
        store = FlakySecretStore()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings(AppSettings(api_key="sk-openai"), path=path, secret_store=store)
            store.failing = {"api_key.openai"}
            with self.assertRaises(SettingsError):
                load_settings(path=path, secret_store=store)
            # Another provider's unreadable key does not fail the load.
            store.failing = {"api_key.gemini"}
            loaded = load_settings(path=path, secret_store=store)

        self.assertEqual(loaded.api_key, "sk-openai")
        self.assertEqual(loaded.unread_keys, frozenset({"gemini"}))


if __name__ == "__main__":
    unittest.main()
