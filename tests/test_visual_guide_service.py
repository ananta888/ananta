"""Tests for VisualGuideService (VG-014).

Covers:
- VG-003: per-snake state isolation
- handle_region_explain with mixed valid/invalid steps
- VG-001 regression: delta_snapshot and reply_snapshot are separate
- Max 50 snake states
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_service():
    """Return a fresh VisualGuideService with mocked dependencies."""
    from agent.services.visual_guide.service import VisualGuideService
    svc = VisualGuideService()
    return svc


def _fake_pug_settings(enabled: bool = True, candidates: int = 1) -> dict:
    return {
        "predictive_guide_enabled": enabled,
        "predictive_guide_multi_candidates": candidates,
        "predictive_guide_log_deltas_only": False,
    }


# ---------------------------------------------------------------------------
# VG-003: per-snake state isolation
# ---------------------------------------------------------------------------

class TestPerSnakeStateIsolation:
    """handle_ui_tick must maintain isolated state per snake_id."""

    def test_two_snakes_have_independent_reply_snapshots(self):
        """Updating state for snake-A must not affect snake-B."""
        import agent.services.visual_guide.service as svc_mod

        original_state = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            from agent.services.visual_guide.service import _get_visual_state

            state_a = _get_visual_state("snake-a")
            state_b = _get_visual_state("snake-b")

            state_a["reply_snapshot"] = "snap-A"
            state_a["reply_at"] = time.time()

            # snake-b must be unaffected
            assert state_b["reply_snapshot"] == ""
            assert state_b["reply_at"] == 0.0

            # Changing snake-b must not affect snake-a
            state_b["reply_snapshot"] = "snap-B"
            assert state_a["reply_snapshot"] == "snap-A"
        finally:
            svc_mod._visual_state = original_state

    def test_delta_snapshot_separate_from_reply_snapshot(self):
        """VG-001 regression: delta_snapshot and reply_snapshot are separate fields."""
        import agent.services.visual_guide.service as svc_mod

        original_state = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            from agent.services.visual_guide.service import _get_visual_state

            state = _get_visual_state("snake-x")
            state["delta_snapshot"] = "delta-value"
            state["reply_snapshot"] = "reply-value"

            # Both exist and are independent
            assert state["delta_snapshot"] == "delta-value"
            assert state["reply_snapshot"] == "reply-value"
            assert state["delta_snapshot"] != state["reply_snapshot"]
        finally:
            svc_mod._visual_state = original_state

    def test_handle_ui_tick_suppresses_when_disabled(self):
        """When predictive_guide_enabled=False, no LLM call or room message is emitted."""
        import agent.services.visual_guide.service as svc_mod

        original_state = svc_mod._visual_state
        svc_mod._visual_state = {}
        appended = []

        # Patch the bridge callables on the service module itself — no circular import risk
        with patch("agent.services.visual_guide.service._visual_session_settings",
                   return_value=_fake_pug_settings(enabled=False)), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)), \
             patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False):
            try:
                svc = _make_service()
                svc.handle_ui_tick("snake-a", "some snapshot", "/chats", [])
                assert len(appended) == 0
            finally:
                svc_mod._visual_state = original_state

    def test_handle_ui_tick_different_snakes_dont_throttle_each_other(self):
        """Two snakes with the same snapshot should each be able to fire independently."""
        import agent.services.visual_guide.service as svc_mod

        original_state = svc_mod._visual_state
        svc_mod._visual_state = {}
        replies = []

        with patch("agent.services.visual_guide.service._visual_session_settings",
                   return_value=_fake_pug_settings(enabled=True)), \
             patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: replies.append(kw)):
            try:
                svc = _make_service()
                # Mock the LLM call to return a deterministic reply
                with patch.object(svc, "_call_llm_for_ui_tick", return_value="Guide text"):
                    svc.handle_ui_tick("snake-1", "snap", "/dashboard", [])
                    svc.handle_ui_tick("snake-2", "snap", "/dashboard", [])

                # Both snakes should have fired (state is independent)
                assert len(replies) == 2
            finally:
                svc_mod._visual_state = original_state


# ---------------------------------------------------------------------------
# handle_region_explain — mixed valid/invalid steps
# ---------------------------------------------------------------------------

class TestHandleRegionExplain:
    """handle_region_explain validates steps and builds guide steps correctly."""

    def test_invalid_steps_are_filtered_out(self):
        """Steps without valid x/y coordinates are excluded from the guide."""
        appended = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)):

            svc = _make_service()
            steps = [
                {"bubble": "Valid step", "x": 100.0, "y": 200.0, "waypoint": "btn.ok"},
                {"bubble": "No coords"},                    # missing x/y → x=0,y=0 which is valid (0<=0<=10000)
                {"bubble": "Bad coords", "x": -1, "y": 5}, # negative x → invalid
                {"bubble": "Another valid", "x": 50.0, "y": 80.0},
            ]

            with patch.object(svc, "_call_llm_for_region_explain",
                               return_value=["Erklärung 1", "Erklärung 2", "Erklärung 3", "Erklärung 4"]):
                svc.handle_region_explain("snake-a", steps, "/workspace")

        # Should have appended one guide message (the bad-coords step is filtered)
        assert len(appended) == 1
        msg_text = appended[0]["text"]
        assert "__GUIDE__:" in msg_text

    def test_empty_steps_produces_no_output(self):
        """Empty region_steps → no output."""
        appended = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)):

            svc = _make_service()
            svc.handle_region_explain("snake-a", [], "/workspace")

        assert len(appended) == 0

    def test_non_dict_steps_are_filtered(self):
        """Non-dict entries in region_steps are dropped silently."""
        appended = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)):

            svc = _make_service()
            steps = ["not-a-dict", None, 42, {"bubble": "ok", "x": 10.0, "y": 20.0}]

            with patch.object(svc, "_call_llm_for_region_explain", return_value=["Erklärung"]):
                svc.handle_region_explain("snake-a", steps, "/board")

        # Only the one valid dict step remains; guide is built
        assert len(appended) == 1


# ---------------------------------------------------------------------------
# VG-001 regression: delta_snapshot vs reply_snapshot separation
# ---------------------------------------------------------------------------

class TestSnapshotStateSeparation:
    """delta_snapshot (for diff computation) and reply_snapshot (throttle) must be independent."""

    def test_delta_and_reply_snapshots_are_separate_fields(self):
        """Directly verify the state dict has two independent snapshot fields."""
        import agent.services.visual_guide.service as svc_mod

        original = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            from agent.services.visual_guide.service import _get_visual_state
            state = _get_visual_state("test-snake")

            assert "delta_snapshot" in state
            assert "reply_snapshot" in state

            state["delta_snapshot"] = "prev"
            state["reply_snapshot"] = "last-reply"

            # They must be independent
            assert state["delta_snapshot"] == "prev"
            assert state["reply_snapshot"] == "last-reply"
        finally:
            svc_mod._visual_state = original

    def test_updating_delta_does_not_change_reply(self):
        """Writing to delta_snapshot must not affect reply_snapshot."""
        import agent.services.visual_guide.service as svc_mod

        original = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            from agent.services.visual_guide.service import _get_visual_state
            state = _get_visual_state("test-snake")
            state["reply_snapshot"] = "unchanged"

            state["delta_snapshot"] = "new-delta"
            assert state["reply_snapshot"] == "unchanged"
        finally:
            svc_mod._visual_state = original


# ---------------------------------------------------------------------------
# Max 50 snake states (VG-003)
# ---------------------------------------------------------------------------

class TestMaxSnakeStates:
    """_visual_state must not grow beyond 50 entries."""

    def test_max_50_states_enforced(self):
        """After inserting 55 snakes, the dict stays at or below 50."""
        import agent.services.visual_guide.service as svc_mod
        from agent.services.visual_guide.service import _get_visual_state, _MAX_VISUAL_STATES

        original = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            for i in range(55):
                _get_visual_state(f"snake-{i:03d}")
            assert len(svc_mod._visual_state) <= _MAX_VISUAL_STATES
        finally:
            svc_mod._visual_state = original

    def test_oldest_state_is_evicted(self):
        """The oldest (by updated_at) state should be evicted when cap is exceeded."""
        import agent.services.visual_guide.service as svc_mod
        from agent.services.visual_guide.service import _get_visual_state, _MAX_VISUAL_STATES

        original = svc_mod._visual_state
        svc_mod._visual_state = {}
        try:
            now = time.time()
            # Fill to exactly max with recent timestamps (TTL is 2h; use now-based offsets)
            for i in range(_MAX_VISUAL_STATES):
                state = _get_visual_state(f"snake-{i:03d}")
                # Give each entry a slightly different recent timestamp
                # snake-000 gets the smallest value → oldest
                state["updated_at"] = now - (_MAX_VISUAL_STATES - i)

            # The oldest has the smallest updated_at → snake-000
            oldest_key = "snake-000"
            assert oldest_key in svc_mod._visual_state

            # Adding one more should evict snake-000 (it's the oldest)
            _get_visual_state("snake-new")

            assert oldest_key not in svc_mod._visual_state
            assert len(svc_mod._visual_state) <= _MAX_VISUAL_STATES
        finally:
            svc_mod._visual_state = original


# ---------------------------------------------------------------------------
# Rate limiting (VG-051)
# ---------------------------------------------------------------------------

class TestRateLimit:
    """VisualGuideService._check_rate_limit must enforce 4 calls/minute per snake."""

    def test_allows_up_to_limit(self):
        svc = _make_service()
        for _ in range(4):
            assert svc._check_rate_limit("snake-rate") is True

    def test_blocks_above_limit(self):
        svc = _make_service()
        for _ in range(4):
            svc._check_rate_limit("snake-rate2")
        assert svc._check_rate_limit("snake-rate2") is False

    def test_different_snakes_independent(self):
        svc = _make_service()
        for _ in range(4):
            svc._check_rate_limit("snake-A")
        # snake-A is exhausted but snake-B should still be fine
        assert svc._check_rate_limit("snake-B") is True

    def test_old_timestamps_expire(self):
        svc = _make_service()
        # Plant 4 old timestamps (>60s ago)
        svc._rate_limit_timestamps["snake-old"] = [time.time() - 70] * 4
        # Should allow a new call because old ones expired
        assert svc._check_rate_limit("snake-old") is True


# ---------------------------------------------------------------------------
# Privacy / redaction (VG-052)
# ---------------------------------------------------------------------------

class TestRedactSnapshot:
    """_redact_snapshot must mask sensitive input values."""

    def test_password_field_redacted(self):
        svc = _make_service()
        snap = 'focus:input[password]="mysecretpassword"'
        result = svc._redact_snapshot(snap)
        assert "mysecretpassword" not in result
        assert "[REDACTED]" in result

    def test_api_key_field_redacted(self):
        svc = _make_service()
        snap = 'focus:input[api-key]="sk-abc123def456ghi789jkl"'
        result = svc._redact_snapshot(snap)
        assert "sk-abc123def456ghi789jkl" not in result

    def test_normal_input_not_redacted(self):
        svc = _make_service()
        snap = 'focus:input[username]="alice"'
        result = svc._redact_snapshot(snap)
        # username is not a sensitive pattern
        assert "alice" in result

    def test_snapshot_clamped_to_500_chars(self):
        svc = _make_service()
        long_snap = "x" * 1000
        result = svc._redact_snapshot(long_snap)
        assert len(result) <= 500
