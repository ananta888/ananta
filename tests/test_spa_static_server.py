from __future__ import annotations

import contextlib
import http.server
import threading
import urllib.error
import urllib.request
from pathlib import Path

from scripts.spa_static_server import build_spa_handler


@contextlib.contextmanager
def _server(root: Path):
    handler = build_spa_handler(root)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


def test_spa_static_server_serves_index_for_oidc_callback_route(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with _server(tmp_path) as base_url:
        status, body = _get(f"{base_url}/oidc-callback?state=s&code=c")

    assert status == 200
    assert "<app-root></app-root>" in body


def test_spa_static_server_keeps_missing_assets_as_404(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<app-root></app-root>", encoding="utf-8")

    with _server(tmp_path) as base_url:
        try:
            _get(f"{base_url}/missing.js")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("missing asset unexpectedly returned success")
