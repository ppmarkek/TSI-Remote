"""A loopback-only HTTP server for the desktop UI's static files and lecture media.

The WebView loads the interface from ``http://127.0.0.1:<port>/<token>/``. The
random path token keeps other local processes and web pages from reading lecture
audio or frames; the Host check blocks DNS-rebinding attempts.
"""

from __future__ import annotations

import mimetypes
import re
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

MediaResolver = Callable[[str, str, str], "Path | None"]

_CHUNK_SIZE = 256 * 1024
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json",
}
_MEDIA_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_RANGE = re.compile(r"bytes=(\d*)-(\d*)\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
CONTENT_SECURITY_POLICY = (
    # pywebview builds its JS bridge with ``new Function``, hence 'unsafe-eval'.
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-src 'none'; "
    "form-action 'none'"
)


class LocalServer:
    """Serve ``static_dir`` and resolver-provided media on 127.0.0.1 only."""

    def __init__(
        self,
        static_dir: Path,
        media_resolver: MediaResolver,
        *,
        token: str | None = None,
    ) -> None:
        self.static_dir = static_dir.resolve()
        self.media_resolver = media_resolver
        self.token = token or secrets.token_urlsafe(18)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url
        handler = _make_handler(self)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever,
            name="konspekt-ui-server",
            daemon=True,
        )
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        self._httpd = None
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _make_handler(server: LocalServer) -> type[BaseHTTPRequestHandler]:
    class Handler(_Handler):
        owner = server

    return Handler


class _Handler(BaseHTTPRequestHandler):
    owner: LocalServer
    server_version = "Konspekt"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # The UI is single-user and local; request logs would only add noise.
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._handle(send_body=True)

    def do_HEAD(self) -> None:
        self._handle(send_body=False)

    def _method_not_allowed(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, extra={"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    # ------------------------------------------------------------------ routing
    def _handle(self, *, send_body: bool) -> None:
        try:
            if not self._host_allowed():
                self._send_error(HTTPStatus.FORBIDDEN)
                return
            path = urlsplit(self.path).path
            prefix = f"/{self.owner.token}/"
            if not path.startswith(prefix):
                self._send_error(HTTPStatus.NOT_FOUND)
                return
            relative = unquote(path[len(prefix) :])
            if relative.startswith("media/"):
                self._serve_media(relative[len("media/") :], send_body=send_body)
                return
            self._serve_static(relative or "index.html", send_body=send_body)
        except (ConnectionError, TimeoutError):
            # The WebView routinely aborts media requests while seeking.
            self.close_connection = True

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        name, _, port = host.rpartition(":")
        if not name:
            name, port = host, ""
        if name not in {"127.0.0.1", "localhost"}:
            return False
        return not port or port == str(self.owner.port)

    def _serve_static(self, relative: str, *, send_body: bool) -> None:
        target = _contained(self.owner.static_dir, relative)
        if target is None or not target.is_file():
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = _STATIC_TYPES.get(target.suffix.lower())
        if content_type is None:
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_file(target, content_type, send_body=send_body)

    def _serve_media(self, relative: str, *, send_body: bool) -> None:
        parts = relative.split("/")
        if len(parts) == 2:
            parts.append("")
        if len(parts) != 3 or not all(_SEGMENT.fullmatch(part) for part in parts[:2]):
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        lecture_key, kind, argument = parts
        if argument and not _SEGMENT.fullmatch(argument):
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            target = self.owner.media_resolver(lecture_key, kind, argument)
        except Exception:  # a broken lecture must not crash the server
            target = None
        if target is None or not target.is_file():
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = _MEDIA_TYPES.get(target.suffix.lower()) or (
            mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        )
        self._send_file(target, content_type, send_body=send_body, ranged=True)

    # ------------------------------------------------------------------ helpers
    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)

    def _send_error(self, status: HTTPStatus, *, extra: dict[str, str] | None = None) -> None:
        body = f"{status.value} {status.phrase}".encode()
        self.send_response(status)
        self._common_headers()
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(
        self,
        target: Path,
        content_type: str,
        *,
        send_body: bool,
        ranged: bool = False,
    ) -> None:
        try:
            size = target.stat().st_size
            handle = target.open("rb")
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND)
            return
        with handle:
            start, end = 0, size - 1
            status = HTTPStatus.OK
            range_header = self.headers.get("Range") if ranged else None
            if range_header:
                parsed = _parse_range(range_header, size)
                if parsed is None:
                    self._send_error(
                        HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                        extra={"Content-Range": f"bytes */{size}"},
                    )
                    return
                start, end = parsed
                status = HTTPStatus.PARTIAL_CONTENT
            length = max(0, end - start + 1)
            self.send_response(status)
            self._common_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if ranged:
                self.send_header("Accept-Ranges", "bytes")
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not send_body or length == 0:
                return
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    match = _RANGE.fullmatch(header.strip())
    if match is None or size <= 0:
        return None
    first, last = match.groups()
    if not first and not last:
        return None
    if not first:
        suffix = int(last)
        if suffix <= 0:
            return None
        return max(0, size - suffix), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        return None
    return start, min(end, size - 1)


def _contained(base: Path, relative: str) -> Path | None:
    if "\\" in relative or "\x00" in relative:
        return None
    parts = [part for part in relative.split("/") if part]
    if any(part in {".", ".."} or ":" in part for part in parts):
        return None
    candidate = base.joinpath(*parts) if parts else base
    try:
        resolved = candidate.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    return resolved
