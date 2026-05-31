from __future__ import annotations

from pathlib import Path

APP_DIR = Path("client_surfaces/operator_tui/visual/browser/webrtc_app")


def test_datachannel_js_matches_python_protocol_constants() -> None:
    js = (APP_DIR / "datachannel.js").read_text(encoding="utf-8")
    py = Path("client_surfaces/operator_tui/realtime/datachannel_protocol.py").read_text(encoding="utf-8")
    assert "export const PROTOCOL_VERSION = 1;" in js
    assert "VERSION = 1" in py
    assert "export const MAX_MESSAGE_BYTES = 65536;" in js
    assert "MAX_MESSAGE_BYTES = 65536" in py
    assert "export const CHUNK_SIZE = 32768;" in js
    assert "CHUNK_SIZE = 32768" in py
    for msg_type in [
        "hello",
        "hello_ack",
        "ping",
        "pong",
        "artifact_offer",
        "artifact_accept",
        "artifact_reject",
        "artifact_chunk",
        "artifact_complete",
        "error",
    ]:
        assert f'"{msg_type}"' in js
        assert f'"{msg_type}"' in py


def test_webrtc_app_does_not_load_remote_assets_or_media_probe_by_default() -> None:
    index = (APP_DIR / "index.html").read_text(encoding="utf-8")
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert "https://" not in index
    assert "http://" not in index
    assert 'from "./media_probe.js"' not in app
    assert "getUserMedia" not in app
    assert "getDisplayMedia" not in app
    assert "function signalingMessage" in app
    for field in ["session_id", "sender_id", "recipient_id", "payload", "session_nonce", "message_id", "timestamp"]:
        assert field in app


def test_media_probe_requires_explicit_import_and_call() -> None:
    media_probe = (APP_DIR / "media_probe.js").read_text(encoding="utf-8")
    app = (APP_DIR / "app.js").read_text(encoding="utf-8")
    assert "export async function probeMediaCapabilities()" in media_probe
    assert "getUserMedia" in media_probe
    assert "getDisplayMedia" in media_probe
    assert "probeMediaCapabilities" not in app
