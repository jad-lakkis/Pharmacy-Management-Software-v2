"""``http.server`` adapter: JSON API under /api, static web UI at /."""

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .routes import Api

log = logging.getLogger("pharmacy.http")
WEB_ROOT = (Path(__file__).resolve().parent.parent / "web").resolve()
MAX_BODY = 1_000_000

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:",
}


def make_handler(api: Api) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PharmacyMS/0.1"
        sys_version = ""

        def log_message(self, fmt, *args):  # route through logging
            log.info("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def _api(self, method: str):
            url = urlsplit(self.path)
            query = dict(parse_qsl(url.query))
            body = {}
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._json(413, {"error": "request body too large"})
            if length:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return self._json(400, {"error": "request body is not valid JSON"})
            auth = self.headers.get("Authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            try:
                status, payload = api.handle(method, url.path, query, body, token)
            except Exception:  # unexpected bug: log it, do not leak details
                log.exception("unhandled error on %s %s", method, url.path)
                status, payload = 500, {"error": "internal server error"}
            self._json(status, payload)

        def _static(self):
            path = urlsplit(self.path).path
            if path in ("", "/"):
                path = "/index.html"
            target = (WEB_ROOT / path.lstrip("/")).resolve()
            if WEB_ROOT not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._api("GET")
            return self._static()

        def do_HEAD(self):
            return self.do_GET()

        def do_POST(self):
            return self._api("POST") if self.path.startswith("/api/") else self._json(405, {"error": "method not allowed"})

        def do_PATCH(self):
            return self._api("PATCH") if self.path.startswith("/api/") else self._json(405, {"error": "method not allowed"})

        def do_DELETE(self):
            return self._api("DELETE") if self.path.startswith("/api/") else self._json(405, {"error": "method not allowed"})

    return Handler


def serve(api: Api, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(api))
    httpd.daemon_threads = True
    return httpd
