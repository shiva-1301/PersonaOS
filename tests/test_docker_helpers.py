"""scripts/docker_helpers.py must not crash on non-JSON responses (e.g. the HTML
pages of the Google OAuth callback)."""

import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import docker_helpers  # noqa: E402

RESPONSES = {
    "/html-400": (400, "text/html", b"<html><body>Invalid state</body></html>"),
    "/html-200": (200, "text/html", b"<html><body>Connected</body></html>"),
    "/json-400": (400, "application/json", b'{"error": {"code": "bad", "message": "x"}}'),
    "/json-200": (200, "application/json", b'{"ok": true}'),
    "/empty-204": (204, "text/plain", b""),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        status, ctype, body = RESPONSES[self.path]
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _get(base_url, path):
    return docker_helpers._send(urllib.request.Request(base_url + path))


def test_send_returns_raw_text_for_html_error_pages(base_url):
    status, body = _get(base_url, "/html-400")
    assert status == 400
    assert isinstance(body, str) and "Invalid state" in body


def test_send_returns_raw_text_for_html_success_pages(base_url):
    status, body = _get(base_url, "/html-200")
    assert status == 200
    assert "Connected" in body


def test_send_still_parses_json(base_url):
    assert _get(base_url, "/json-200") == (200, {"ok": True})
    assert _get(base_url, "/json-400") == (400, {"error": {"code": "bad", "message": "x"}})


def test_send_returns_none_for_empty_body(base_url):
    assert _get(base_url, "/empty-204") == (204, None)
