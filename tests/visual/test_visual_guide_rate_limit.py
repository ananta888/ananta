"""VG-051: Rate-limit tests for the Visual Guide Engine.

Tests:
- VisualGuideService._check_rate_limit (per-snake rate limiting)
- VisualGuideService.handle_ui_tick throttle (reply_snapshot deduplication)
- _VISUAL_THROTTLE_S constant is positive
- DecisionService suppression when pug disabled
- time.time mock simulates 60s passage
"""
from __future__ import annotations

import time
import unittest.mock as mock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_visual_state(app):
    """Ensure Flask is initialized; also reset legacy module-level state."""
    import agent.routes.snakes_execution_routes as ser
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0
    yield ser
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0


@pytest.fixture
def svc(monkeypatch):
    """VisualGuideService with background-thread check bypassed.
    Clears _visual_state before and after to prevent cross-test leakage."""
    from agent.services.visual_guide.service import VisualGuideService
    import agent.services.visual_guide.service as svc_mod
    svc_mod._visual_state.clear()
    monkeypatch.setattr(svc_mod, "_background_threads_disabled", lambda: False)
    monkeypatch.setattr(svc_mod, "_visual_session_settings",
                        lambda: {"predictive_guide_enabled": True,
                                 "predictive_guide_multi_candidates": 1})
    service = VisualGuideService()
    yield service
    svc_mod._visual_state.clear()


def _fake_config():
    return {"chat_model": "test-model", "chat_api_base": "", "chat_api_key": "sk-test"}


# ---------------------------------------------------------------------------
# Tests: VisualGuideService throttle via _visual_state (per-snake)
# ---------------------------------------------------------------------------

class TestVisualReplyThrottle:
    def test_throttle_prevents_second_call_within_window(self, svc, monkeypatch):
        """A second handle_ui_tick within _VISUAL_THROTTLE_S is suppressed."""
        import agent.services.visual_guide.service as svc_mod
        appended = []

        monkeypatch.setattr(svc_mod, "_append_room_ai_message",
                            lambda **kw: appended.append(kw))

        with mock.patch.object(svc, "_call_llm_for_ui_tick", return_value="Guide text"):
            # First call — should produce a reply
            svc.handle_ui_tick("s1", "/teams | snap_1", "/teams", [])
            first_count = len(appended)

            # Second call immediately after (< THROTTLE_S elapsed) with DIFFERENT snapshot
            svc.handle_ui_tick("s1", "/chats | snap_2", "/chats", [])
            second_count = len(appended)

        assert first_count >= 1, "First call should have produced a reply"
        assert second_count == first_count, (
            "Second call within throttle window should not append another message"
        )

    def test_throttle_allows_call_after_window_expires(self, svc, monkeypatch):
        """After the throttle window elapses, a new snapshot can produce a reply."""
        import agent.services.visual_guide.service as svc_mod
        appended = []

        monkeypatch.setattr(svc_mod, "_append_room_ai_message",
                            lambda **kw: appended.append(kw))

        with mock.patch.object(svc, "_call_llm_for_ui_tick", return_value="Guide text"):
            # First call to set up state
            svc.handle_ui_tick("s1", "/old | snap", "/old", [])

            # Manually wind back the reply_at timestamp
            from agent.services.visual_guide.service import _get_visual_state
            state = _get_visual_state("s1")
            state["reply_at"] = time.time() - 30.0  # past throttle window (>25s)

            appended.clear()
            svc.handle_ui_tick("s1", "/teams | new_snap", "/teams", [])

        assert len(appended) >= 1, "Reply should have been generated after throttle window"

    def test_throttle_constant_is_positive(self, reset_visual_state):
        """The throttle constant must be positive to be meaningful."""
        import agent.services.visual_guide.service as svc_mod
        assert svc_mod._VISUAL_THROTTLE_S > 0

    def test_same_snapshot_suppressed_on_llm_path(self, svc, monkeypatch):
        """The same snapshot never triggers a second LLM reply regardless of time.

        Uses a route not in ROUTE_TIPS to ensure the LLM path (not rule path) is taken.
        The rule path returns without updating reply_snapshot by design (rules are cheap).
        """
        import agent.services.visual_guide.service as svc_mod
        appended = []

        monkeypatch.setattr(svc_mod, "_append_room_ai_message",
                            lambda **kw: appended.append(kw))

        # Use /unknown-route — not in ROUTE_TIPS → forces LLM path which updates reply_snapshot
        with mock.patch.object(svc, "_call_llm_for_ui_tick", return_value="Guide text"):
            # First call — establishes reply_snapshot via LLM path
            svc.handle_ui_tick("s1", "/unknown | same_snap", "/unknown-route", [])
            count_after_first = len(appended)

            # Wind back time past throttle window
            from agent.services.visual_guide.service import _get_visual_state
            state = _get_visual_state("s1")
            state["reply_at"] = 0.0

            # Same snapshot again — duplicate check should suppress (reply_snapshot is set)
            svc.handle_ui_tick("s1", "/unknown | same_snap", "/unknown-route", [])
            assert len(appended) == count_after_first, (
                "Same snapshot must not produce a second LLM reply even after throttle window"
            )

    def test_time_mock_simulates_60s_elapsed(self, reset_visual_state):
        """Simulating 60s elapsed via manipulating state directly."""
        import agent.services.visual_guide.service as svc_mod
        from agent.services.visual_guide.service import _get_visual_state

        # Set reply_at to "now"
        now = time.time()
        state = _get_visual_state("time-test-snake")
        state["reply_at"] = now

        elapsed = time.time() - state["reply_at"]
        assert elapsed < svc_mod._VISUAL_THROTTLE_S

        # Wind back by 60 seconds
        state["reply_at"] = now - 60.0
        elapsed_after = time.time() - state["reply_at"]
        assert elapsed_after >= svc_mod._VISUAL_THROTTLE_S, (
            "After 60s the throttle window must have elapsed"
        )


# ---------------------------------------------------------------------------
# Tests: VisualGuideService._check_rate_limit (4 calls per minute cap)
# ---------------------------------------------------------------------------

class TestServiceRateLimit:
    def test_first_four_calls_allowed(self, svc):
        """Rate limit allows the first 4 LLM calls within a minute."""
        for i in range(4):
            assert svc._check_rate_limit("snake-rl") is True

    def test_fifth_call_is_blocked(self, svc):
        """The 5th call within a minute is blocked."""
        for _ in range(4):
            svc._check_rate_limit("snake-rl")
        assert svc._check_rate_limit("snake-rl") is False

    def test_different_snake_ids_have_independent_limits(self, svc):
        """Rate limit buckets are per-snake-id — snake B is not blocked by snake A."""
        for _ in range(4):
            svc._check_rate_limit("snake-a")
        # snake-a is at limit; snake-b should still be allowed
        assert svc._check_rate_limit("snake-b") is True

    def test_old_timestamps_are_evicted(self, svc):
        """Timestamps older than 60s are removed, allowing new calls."""
        # Add 4 "old" timestamps
        old_ts = time.time() - 70.0
        svc._rate_limit_timestamps["snake-old"] = [old_ts, old_ts, old_ts, old_ts]
        # All are expired → call should be allowed
        assert svc._check_rate_limit("snake-old") is True


# ---------------------------------------------------------------------------
# Tests: DecisionService suppression (visual_guide package)
# ---------------------------------------------------------------------------

class TestDecisionServiceRateLimit:
    """Tests for VisualGuideDecisionService.decide() suppression logic."""

    def test_suppressed_when_pug_disabled(self):
        """With predictive_guide_enabled=False the decision is suppressed."""
        from agent.services.visual_guide.decision_service import VisualGuideDecisionService
        from agent.services.visual_guide.models import VisualGuideRequest

        dec_svc = VisualGuideDecisionService()
        req = VisualGuideRequest(
            snake_id="s1",
            trigger_type="ui_tick",
            route="/teams",
            snapshot="/teams | nav:Teams*",
        )
        decision = dec_svc.decide(req, pug_settings={"predictive_guide_enabled": False})
        assert decision.strategy == "suppressed"
        assert "predictive_guide_enabled" in decision.reason

    def test_not_suppressed_when_pug_enabled(self):
        """With predictive_guide_enabled=True the decision is NOT suppressed."""
        from agent.services.visual_guide.decision_service import VisualGuideDecisionService
        from agent.services.visual_guide.models import VisualGuideRequest

        dec_svc = VisualGuideDecisionService()
        req = VisualGuideRequest(
            snake_id="s1",
            trigger_type="ui_tick",
            route="/workspace",
            snapshot="/workspace | nav:Workspace*",
        )
        decision = dec_svc.decide(req, pug_settings={"predictive_guide_enabled": True})
        assert decision.strategy != "suppressed"

    def test_region_explain_never_suppressed(self):
        """trigger_type=region_explain is always processed (LLM strategy)."""
        from agent.services.visual_guide.decision_service import VisualGuideDecisionService
        from agent.services.visual_guide.models import VisualGuideRequest

        dec_svc = VisualGuideDecisionService()
        req = VisualGuideRequest(
            snake_id="s1",
            trigger_type="region_explain",
            route="/teams",
            snapshot="",
            region_steps=[{"bubble": "btn", "x": 100, "y": 200}],
        )
        decision = dec_svc.decide(req, pug_settings={"predictive_guide_enabled": False})
        # region_explain bypasses the suppression check
        assert decision.strategy == "llm"
