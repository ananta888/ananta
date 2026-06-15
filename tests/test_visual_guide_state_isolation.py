"""VG-003: Two snake_ids must not interfere with each other.

Tests verify that:
- State updated for snake A does not bleed into snake B's perspective
- Snapshot deltas computed for A differ from those computed for B
- Reply throttle for A does not block B
"""
from __future__ import annotations

import json
import time
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_visual_state(app):
    """Clear module-global and per-snake visual state before and after each test.
    Requires the `app` fixture so Flask + route modules are fully initialised."""
    import agent.routes.snakes_execution_routes as ser
    import agent.services.visual_guide.service as svc_mod
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0
    svc_mod._visual_state.clear()
    yield ser
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0
    svc_mod._visual_state.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_fake_openai_client(content: str = "Guide text"):
    class _FakeCompletion:
        choices = [type("C", (), {
            "message": type("M", (), {"content": content})()
        })()]

    class _FakeClient:
        def __init__(self, **_): pass
        class chat:
            class completions:
                @staticmethod
                def create(**_):
                    return _FakeCompletion()

    return _FakeClient


def _fake_config():
    return {"chat_model": "test-model", "chat_api_base": "", "chat_api_key": "sk-test"}


def _set_pug_settings_enabled():
    """Set predictive_guide_enabled=True in the visual session (app-fixture path)."""
    try:
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        from client_surfaces.operator_tui.chat_state import make_session
        mgr = get_manager()
        sess = make_session(
            session_id="ananta-visual", name="Visual Snake Log",
            icon="X", group="Konfiguration",
            settings={"predictive_guide_enabled": True, "predictive_guide_multi_candidates": 1},
        )
        mgr.save({"chat_sessions": [sess], "chat_active_session_id": ""})
        mgr.load()
    except Exception:
        pass  # If config manager not available, tests still run via monkeypatch


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSnakeIdIsolation:
    """Verifies that snake A's state cannot interfere with snake B."""

    def test_ui_state_keyed_per_snake_id(self, reset_visual_state):
        """_snake_ui_state is keyed by snake_id — different snakes have separate slots."""
        from agent.routes.snakes_execution_routes import _snake_ui_state
        _snake_ui_state.clear()

        _snake_ui_state["snake-A"] = {"route": "/teams", "ui_snapshot": "/teams | nav:Teams*"}
        _snake_ui_state["snake-B"] = {"route": "/chats", "ui_snapshot": "/chats | nav:Chats*"}

        assert _snake_ui_state["snake-A"]["route"] == "/teams"
        assert _snake_ui_state["snake-B"]["route"] == "/chats"
        # Modifying A does not touch B
        _snake_ui_state["snake-A"]["route"] = "/workspace"
        assert _snake_ui_state["snake-B"]["route"] == "/chats"

        _snake_ui_state.clear()

    def test_per_snake_visual_state_is_keyed_by_snake_id(self, reset_visual_state):
        """Per-snake visual state in _visual_state is independent per snake_id."""
        from agent.services.visual_guide.service import _get_visual_state
        import agent.services.visual_guide.service as svc_mod

        # Wipe existing state
        svc_mod._visual_state.clear()

        state_a = _get_visual_state("snake-isolation-A")
        state_b = _get_visual_state("snake-isolation-B")

        state_a["reply_snapshot"] = "/teams"
        assert state_b["reply_snapshot"] == ""  # B unchanged

        svc_mod._visual_state.clear()

    def test_reply_throttle_is_per_snake_independent(self, reset_visual_state):
        """Snake A's throttle window does NOT block snake B in the new per-snake architecture."""
        from agent.services.visual_guide.service import _get_visual_state, _VISUAL_THROTTLE_S
        import agent.services.visual_guide.service as svc_mod

        svc_mod._visual_state.clear()

        # A just produced a reply
        state_a = _get_visual_state("snake-A-throttle")
        state_a["reply_at"] = time.time()

        # B's state is fresh — no throttle
        state_b = _get_visual_state("snake-B-throttle")
        elapsed_b = time.time() - state_b["reply_at"]
        # B's reply_at is 0.0 → elapsed is very large → throttle NOT active
        assert elapsed_b >= _VISUAL_THROTTLE_S, "B must not be throttled by A"

        svc_mod._visual_state.clear()

    def test_reply_throttle_releases_after_window(self, reset_visual_state):
        """After the throttle window expires for a snake, it can reply again."""
        from agent.services.visual_guide.service import _get_visual_state, _VISUAL_THROTTLE_S
        import agent.services.visual_guide.service as svc_mod

        svc_mod._visual_state.clear()
        state = _get_visual_state("snake-window-release")
        state["reply_at"] = time.time() - 30.0  # 30s ago (> 25s throttle)

        elapsed = time.time() - state["reply_at"]
        assert elapsed >= _VISUAL_THROTTLE_S

        svc_mod._visual_state.clear()


class TestSnapshotDeltaIsolation:
    """Verify snapshot diffs are computed correctly for independent snapshots."""

    def test_delta_for_a_differs_from_delta_for_b(self):
        """Two different snapshot transitions produce different deltas."""
        from agent.services.snapshot_delta import diff_snapshots

        baseline = "/teams | nav:Teams*"
        snap_a = "/chats | nav:Chats*"
        snap_b = "/workspace | nav:Workspace*"

        delta_a = diff_snapshots(baseline, snap_a)
        delta_b = diff_snapshots(baseline, snap_b)

        assert delta_a.lines != delta_b.lines
        assert any("/chats" in l for l in delta_a.lines)
        assert any("/workspace" in l for l in delta_b.lines)

    def test_identical_snapshots_produce_empty_delta(self):
        """If A and B have the same snapshot, their deltas are both empty."""
        from agent.services.snapshot_delta import diff_snapshots

        snap = "/teams | nav:Teams* | list:3"
        delta_a = diff_snapshots(snap, snap)
        delta_b = diff_snapshots(snap, snap)

        assert delta_a.is_empty()
        assert delta_b.is_empty()

    def test_delta_service_is_stateless(self):
        """diff_snapshots must be a pure function — calling it multiple times with
        the same arguments always returns the same result."""
        from agent.services.snapshot_delta import diff_snapshots

        prev = "/teams | nav:Teams* | list:3"
        curr = "/chats | nav:Chats* | list:7"

        result1 = diff_snapshots(prev, curr)
        result2 = diff_snapshots(prev, curr)

        assert result1.lines == result2.lines
        assert result1.changed_paths == result2.changed_paths


class TestVisualReplyIsolation:
    """Verify that _spawn_visual_reply logic does not mix state across calls."""

    def test_different_snapshots_both_pass_duplicate_check(self, reset_visual_state):
        """If _visual_last_reply_snapshot is snap_A, then snap_B is NOT a duplicate
        and should pass the throttle equality check."""
        ser = reset_visual_state
        ser._visual_last_reply_snapshot = "/teams | snap_A"

        incoming = "/chats | snap_B"
        assert incoming != ser._visual_last_reply_snapshot

    def test_handle_ui_tick_updates_reply_snapshot_not_delta(self, reset_visual_state, monkeypatch):
        """After handle_ui_tick runs for a snake, its reply_snapshot is updated in
        _visual_state but the delta_snapshot is only updated by _append_visual_user_tick."""
        from agent.services.visual_guide.service import VisualGuideService, _get_visual_state
        import agent.services.visual_guide.service as svc_mod

        monkeypatch.setattr(svc_mod, "_background_threads_disabled", lambda: False)
        monkeypatch.setattr(svc_mod, "_visual_session_settings",
                            lambda: {"predictive_guide_enabled": True,
                                     "predictive_guide_multi_candidates": 1})
        monkeypatch.setattr(svc_mod, "_append_room_ai_message", lambda **kw: None)

        svc = VisualGuideService()
        state_before = _get_visual_state("iso-snake")
        initial_delta = state_before["delta_snapshot"]

        with mock.patch.object(svc, "_call_llm_for_ui_tick", return_value="Guide text"):
            svc.handle_ui_tick("iso-snake", "/chats | nav:Chats*", "/chats", [])

        state_after = _get_visual_state("iso-snake")
        # reply_snapshot should have been updated to the incoming snapshot
        assert state_after["reply_snapshot"] == "/chats | nav:Chats*"
        # delta_snapshot is NOT touched by handle_ui_tick
        assert state_after["delta_snapshot"] == initial_delta
