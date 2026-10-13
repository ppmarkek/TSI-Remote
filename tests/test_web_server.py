from __future__ import annotations

import http.client
import tempfile
import unittest
from pathlib import Path

from konspekt.web.server import LocalServer, _parse_range


class LocalServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.static = root / "static"
        self.static.mkdir()
        (self.static / "index.html").write_text("<p>Привет</p>", encoding="utf-8")
        (self.static / "app.js").write_text("console.log(1)", encoding="utf-8")
        (self.static / "secret.py").write_text("print(1)", encoding="utf-8")
        (root / "outside.html").write_text("outside", encoding="utf-8")
        self.audio = root / "audio.wav"
        self.audio.write_bytes(bytes(range(100)))
        self.requests: list[tuple[str, str, str]] = []

        def resolver(key: str, kind: str, argument: str) -> Path | None:
            self.requests.append((key, kind, argument))
            if key == "boom":
                raise RuntimeError("broken lecture")
            return self.audio if kind == "audio" else None

        self.server = LocalServer(self.static, resolver, token="tok")
        self.server.start()
        self.addCleanup(self.server.stop)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_serves_static_files_under_token(self) -> None:
        status, headers, body = self.request("/tok/")

        self.assertEqual(status, 200)
        self.assertEqual(body.decode("utf-8"), "<p>Привет</p>")
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertTrue(self.server.base_url.endswith("/tok/"))

    def test_rejects_missing_token_and_unknown_types(self) -> None:
        self.assertEqual(self.request("/index.html")[0], 404)
        self.assertEqual(self.request("/other/index.html")[0], 404)
        self.assertEqual(self.request("/tok/secret.py")[0], 404)

    def test_rejects_paths_outside_static_directory(self) -> None:
        for path in (
            "/tok/../outside.html",
            "/tok/%2e%2e/outside.html",
            "/tok/..%2foutside.html",
            "/tok/..%5coutside.html",
            "/tok/C:%5cWindows%5cwin.ini",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_rejects_foreign_host_header(self) -> None:
        status, _, _ = self.request("/tok/", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/tok/", headers={"Host": "localhost:1"})
        self.assertEqual(status, 403)

    def test_only_get_and_head_are_allowed(self) -> None:
        status, headers, _ = self.request("/tok/", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")

        status, headers, body = self.request("/tok/app.js", method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(headers["Content-Length"], "14")

    def test_media_supports_ranges(self) -> None:
        status, headers, body = self.request("/tok/media/abc123/audio")
        self.assertEqual((status, len(body)), (200, 100))
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(headers["Content-Type"], "audio/wav")

        status, headers, body = self.request(
            "/tok/media/abc123/audio", headers={"Range": "bytes=10-19"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, bytes(range(10, 20)))
        self.assertEqual(headers["Content-Range"], "bytes 10-19/100")

        status, headers, _ = self.request(
            "/tok/media/abc123/audio", headers={"Range": "bytes=500-"}
        )
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */100")

    def test_media_validates_segments_and_survives_resolver_errors(self) -> None:
        self.assertEqual(self.request("/tok/media/abc123/frame/12")[0], 404)
        self.assertEqual(self.requests[-1], ("abc123", "frame", "12"))
        self.assertEqual(self.request("/tok/media/boom/audio")[0], 404)

        calls = len(self.requests)
        self.assertEqual(self.request("/tok/media/a.b/audio")[0], 404)
        self.assertEqual(self.request("/tok/media/abc/audio/x/y")[0], 404)
        self.assertEqual(len(self.requests), calls)

    def test_parse_range(self) -> None:
        self.assertEqual(_parse_range("bytes=0-", 10), (0, 9))
        self.assertEqual(_parse_range("bytes=-3", 10), (7, 9))
        self.assertEqual(_parse_range("bytes=5-100", 10), (5, 9))
        self.assertIsNone(_parse_range("bytes=-", 10))
        self.assertIsNone(_parse_range("bytes=7-3", 10))
        self.assertIsNone(_parse_range("bytes=0-1,4-5", 10))
        self.assertIsNone(_parse_range("bytes=0-", 0))


if __name__ == "__main__":
    unittest.main()
