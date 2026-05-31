from __future__ import annotations

import json
import urllib.request
import urllib.error

from client_surfaces.operator_tui.windowing.bridge_server import ExternalWindowBridgeServer
from client_surfaces.operator_tui.windowing.protocol import allowed_actions, is_allowed_action


def test_window_action_allowlist_contains_expected_actions() -> None:
    actions = set(allowed_actions())
    assert "snake.pause" in actions
    assert "snake.resume" in actions
    assert "view.next" in actions
    assert "view.previous" in actions
    assert "view.doc" in actions
    assert "view.snake" in actions


def test_window_action_rejects_unknown_action() -> None:
    assert is_allowed_action("view.next") is True
    assert is_allowed_action("totally.unknown.action") is False


def test_bridge_rejects_duplicate_event_ids() -> None:
    bridge = ExternalWindowBridgeServer()
    bridge.start()
    try:
        token = bridge.session_token
        base = f"http://127.0.0.1:{bridge.status().port}"
        payload = {"action_id": "view.next", "args": {}, "event_id": "dup-1"}
        req1 = urllib.request.Request(
            f"{base}/action",
            method="POST",
            headers={"Content-Type": "application/json", "X-Ananta-Window-Token": token},
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req1, timeout=2) as r1:
            assert r1.status == 202
        req2 = urllib.request.Request(
            f"{base}/action",
            method="POST",
            headers={"Content-Type": "application/json", "X-Ananta-Window-Token": token},
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            urllib.request.urlopen(req2, timeout=2)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            assert exc.code == 409
            assert body.get("reason_code") == "window_bridge_duplicate_event"
    finally:
        bridge.stop()
