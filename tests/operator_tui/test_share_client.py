"""Tests for operator TUI share client request contracts."""
from __future__ import annotations

from client_surfaces.operator_tui import share_client


def test_join_session_sends_device_id_and_fingerprint(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(url: str, body: dict[str, object], token: str) -> dict[str, object]:
        captured.update({"url": url, "body": body, "token": token})
        return {"ok": True}

    monkeypatch.setattr(share_client, "_post", fake_post)

    result = share_client.join_session(
        token="tok",
        invite_code="CODE123",
        session_id="session-1",
        device_id="fp-1",
        device_fingerprint="fp-1",
        base_url="https://webrtc.ananta.de",
    )

    assert result == {"ok": True}
    assert captured["url"] == "https://webrtc.ananta.de/rendezvous/sessions/session-1/join"
    assert captured["token"] == "tok"
    assert captured["body"] == {
        "invite_code": "CODE123",
        "device_id": "fp-1",
        "device_fingerprint": "fp-1",
    }
