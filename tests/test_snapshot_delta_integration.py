"""Integration tests for snapshot-delta persistence in the ananta-visual
log session, and the /api/snapshot/diff REST endpoint."""
from __future__ import annotations

import json
import pytest


def _build_session(*, log_deltas_only: bool):
    """Build a persisted ananta-visual chat session via the manager.

    This goes directly through the manager to avoid the chat_state
    re-sync path (which would re-add the built-in ananta-visual default
    and overwrite our log_deltas_only override on every save)."""
    from client_surfaces.operator_tui.config.user_config_manager import get_manager
    from client_surfaces.operator_tui.chat_state import make_session
    mgr = get_manager()
    sess = make_session(
        session_id="ananta-visual", name="Visual Snake Log",
        icon="🐍", group="Konfiguration",
        settings={"predictive_guide_log_deltas_only": log_deltas_only},
    )
    mgr.save({"chat_sessions": [sess], "chat_active_session_id": ""})
    mgr.load()
    return mgr, sess


@pytest.fixture
def reset_visual_state():
    """Reset module-global state in snakes_execution_routes before AND after
    each test so delta tracking doesn't leak into other test files."""
    import importlib
    ser = importlib.import_module("agent.routes.snakes_execution_routes")
    ser._visual_last_snapshot = ""
    ser._visual_last_reply_at = 0.0
    yield ser
    ser._visual_last_snapshot = ""
    ser._visual_last_reply_at = 0.0


# ── Persistence: delta alongside the raw tick ───────────────────────────────


def test_visual_tick_persists_delta_when_log_deltas_only_enabled(app, reset_visual_state):
    """When the session has predictive_guide_log_deltas_only=True, the
    backend stores a SECOND message containing the human-readable delta
    in addition to the raw [ui-tick] snapshot."""
    from agent.routes.snakes import _room_messages
    from tests.test_snakes_chat_api import _register
    _room_messages.clear()
    _build_session(log_deltas_only=True)

    client = app.test_client()
    s1 = _register(client, "DeltaSnake")
    headers = {"Authorization": f"Bearer {s1['token']}"}

    # First tick — establishes baseline
    snap1 = "/teams | nav:Teams* | h:Teams | list:3"
    client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": f"[ui-tick] {snap1}",
              "visibility": "system", "session_id": "ananta-visual",
              "ui_context": {"ui_snapshot": snap1}},
        headers=headers,
    )
    # Second tick — different snapshot
    snap2 = "/chats | nav:Chats* | h:Chats | list:7"
    client.post(
        f"/snakes/{s1['id']}/chat/messages",
        json={"channel_type": "room", "text": f"[ui-tick] {snap2}",
              "visibility": "system", "session_id": "ananta-visual",
              "ui_context": {"ui_snapshot": snap2}},
        headers=headers,
    )

    visual_msgs = [m for m in _room_messages if m.get("session_id") == "ananta-visual"]
    # Expect 2 raw ticks + 1 delta message (the second tick produces a delta
    # because log_deltas_only is on, and the first tick had no baseline yet)
    assert len(visual_msgs) == 3
    # Find the delta message
    delta_msgs = [m for m in visual_msgs if m.get("text", "").startswith("[ui-delta]")]
    assert len(delta_msgs) == 1
    delta_text = delta_msgs[0]["text"]
    assert "/teams" in delta_text and "/chats" in delta_text
    assert delta_msgs[0]["visibility"] == "system"
    assert delta_msgs[0]["sender_id"] == "browser"


def test_visual_tick_persists_only_raw_when_log_deltas_only_disabled(app, reset_visual_state):
    """When the session has predictive_guide_log_deltas_only=False (default),
    only the raw [ui-tick] is persisted, no extra delta message."""
    from agent.routes.snakes import _room_messages
    from tests.test_snakes_chat_api import _register
    _room_messages.clear()
    _build_session(log_deltas_only=False)

    client = app.test_client()
    s1 = _register(client, "FullSnapSnake")
    headers = {"Authorization": f"Bearer {s1['token']}"}

    snap1 = "/teams | nav:Teams*"
    snap2 = "/chats | nav:Chats*"
    for s in (snap1, snap2):
        client.post(
            f"/snakes/{s1['id']}/chat/messages",
            json={"channel_type": "room", "text": f"[ui-tick] {s}",
                  "visibility": "system", "session_id": "ananta-visual",
                  "ui_context": {"ui_snapshot": s}},
            headers=headers,
        )

    visual_msgs = [m for m in _room_messages if m.get("session_id") == "ananta-visual"]
    assert len(visual_msgs) == 2  # only the two raw ticks, no delta
    assert not any(m.get("text", "").startswith("[ui-delta]") for m in visual_msgs)


# ── REST endpoint /api/snapshot/diff ─────────────────────────────────────────


def test_snapshot_diff_endpoint_returns_delta():
    """POST /api/snapshot/diff with prev+curr returns a structured delta."""
    from flask import Flask
    from agent.routes.snapshot_diff_api import snapshot_diff_bp

    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snapshot_diff_bp)
    c = a.test_client()
    resp = c.post("/api/snapshot/diff", json={
        "prev": "/teams | nav:Teams* | list:3",
        "curr": "/chats | nav:Chats* | list:7",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "lines" in data
    assert "changed_paths" in data
    assert "/teams → /chats" in data["changed_paths"]
    assert any("list" in l and "3" in l and "7" in l for l in data["lines"])


def test_snapshot_diff_endpoint_handles_empty_prev():
    from flask import Flask
    from agent.routes.snapshot_diff_api import snapshot_diff_bp

    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snapshot_diff_bp)
    c = a.test_client()
    resp = c.post("/api/snapshot/diff", json={"prev": "", "curr": "/teams"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["lines"] == []
    assert data["changed_paths"] == []


def test_snapshot_diff_endpoint_rejects_missing_fields():
    from flask import Flask
    from agent.routes.snapshot_diff_api import snapshot_diff_bp

    a = Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(snapshot_diff_bp)
    c = a.test_client()
    resp = c.post("/api/snapshot/diff", json={"prev": "/teams"})
    assert resp.status_code == 400
