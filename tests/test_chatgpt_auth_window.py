from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from konspekt.chatgpt_auth_window import (
    AUTH_URL_ENV,
    ChatGPTAuthWindowError,
    _validated_auth_url,
    main,
    run_auth_window,
)


class FakeWebview:
    def __init__(self) -> None:
        self.window_args = None
        self.window_kwargs = None
        self.start_kwargs = None

    def create_window(self, *args: object, **kwargs: object) -> object:
        self.window_args = args
        self.window_kwargs = kwargs
        return object()

    def start(self, **kwargs: object) -> None:
        self.start_kwargs = kwargs


class ChatGPTAuthWindowTests(unittest.TestCase):
    def test_opens_only_the_validated_url_in_private_edge_webview_on_windows(self) -> None:
        fake = FakeWebview()
        auth_url = "https://auth.openai.com/oauth/authorize?state=secret"
        with patch.dict(os.environ, {AUTH_URL_ENV: auth_url}, clear=False):
            run_auth_window(webview_module=fake, system="win32")
            self.assertNotIn(AUTH_URL_ENV, os.environ)

        self.assertEqual(fake.window_args[1], auth_url)
        self.assertEqual(fake.start_kwargs["gui"], "edgechromium")
        self.assertEqual(fake.start_kwargs["private_mode"], True)
        self.assertEqual(fake.start_kwargs["debug"], False)

    def test_opens_in_cocoa_wkwebview_on_macos(self) -> None:
        fake = FakeWebview()
        auth_url = "https://auth.openai.com/oauth/authorize?state=secret"
        run_auth_window(auth_url=auth_url, webview_module=fake, system="darwin")

        self.assertEqual(fake.window_args[1], auth_url)
        self.assertEqual(fake.start_kwargs["gui"], "cocoa")
        self.assertEqual(fake.start_kwargs["private_mode"], True)

    def test_rejects_lookalike_and_insecure_hosts(self) -> None:
        rejected = [
            "http://auth.openai.com/oauth/authorize",
            "https://auth.openai.com.evil.example/oauth/authorize",
            "https://user@auth.openai.com/oauth/authorize",
            "https://auth.openai.com:8443/oauth/authorize",
            "https://example.test/oauth/authorize",
        ]
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ChatGPTAuthWindowError):
                    _validated_auth_url(value)

    def test_accepts_supported_exact_hosts(self) -> None:
        for value in (
            "https://auth.openai.com/oauth/authorize",
            "https://chatgpt.com/auth/login",
        ):
            with self.subTest(value=value):
                self.assertEqual(_validated_auth_url(value), value)

    def test_helper_main_rejects_unexpected_arguments(self) -> None:
        self.assertEqual(main(["https://auth.openai.com"]), 2)


if __name__ == "__main__":
    unittest.main()
