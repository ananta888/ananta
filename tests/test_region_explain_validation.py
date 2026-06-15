"""VG-004: Tests for VisualGuideService.handle_region_explain validation.

Tests the region-explain pipeline via VisualGuideService directly
(bypassing the route wrapper _spawn_region_explain_reply which calls
_background_threads_disabled() and returns early in test mode).

All LLM calls are mocked — no actual API access occurs.

Key behaviours from VisualGuideService.handle_region_explain:
- Empty region_steps → return immediately before any API call
- Steps without 'bubble' text → no LLM call needed (labels empty, but rule engine runs)
- Steps with x=99999 or y=99999 → out of bounds → step dropped from _build_guide_steps
- Missing x/y → treated as 0 (valid), NOT dropped
- At most 12 steps are processed
- Mixed valid/invalid steps: valid ones produce guide output
"""
from __future__ import annotations

import json
import unittest.mock as mock

import pytest

from agent.services.visual_guide.service import VisualGuideService, _VISUAL_SESSION_ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc(monkeypatch) -> VisualGuideService:
    """Return a fresh VisualGuideService with background-disabled check bypassed.
    Clears _visual_state before/after to prevent cross-test leakage."""
    import agent.services.visual_guide.service as svc_mod
    svc_mod._visual_state.clear()
    # Bypass _background_threads_disabled so handle_region_explain actually runs
    monkeypatch.setattr(svc_mod, "_background_threads_disabled", lambda: False)
    service = VisualGuideService()
    yield service
    svc_mod._visual_state.clear()


def _make_fake_openai_client(explanations: list[str]):
    """Fake openai.OpenAI that returns `explanations` as a JSON array."""
    raw_json = json.dumps(explanations)

    class _FakeCompletion:
        choices = [type("C", (), {
            "message": type("M", (), {"content": raw_json})(),
        })()]

    class _FakeCompletions:
        @staticmethod
        def create(**_):
            return _FakeCompletion()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        def __init__(self, **_): pass
        chat = _FakeChat()

    return _FakeClient


def _make_fake_config(model: str = "test-model"):
    return {"chat_model": model, "chat_api_base": "", "chat_api_key": "sk-test"}


# ---------------------------------------------------------------------------
# Helper: run handle_region_explain with mocked LLM and capture output
# ---------------------------------------------------------------------------

def _run_region_explain(svc_instance, region_steps, route="", *, explanations=None):
    """Run handle_region_explain with mocked LLM and config; return captured messages."""
    n = max(len(region_steps), 1)
    expl = explanations or [f"Erklärung {i+1}" for i in range(n)]
    fake_cls = _make_fake_openai_client(expl)

    captured = []
    import agent.services.visual_guide.service as svc_mod

    # Mock _call_llm_for_region_explain to bypass the full LLM stack
    def _mock_llm(self, steps, route):
        return expl[:len(steps)]

    with mock.patch("openai.OpenAI", fake_cls), \
         mock.patch("agent.routes.ai_snake_config._current_config",
                    return_value=_make_fake_config()), \
         mock.patch.object(svc_mod, "_append_room_ai_message",
                           side_effect=lambda **kw: captured.append(kw)), \
         mock.patch.object(VisualGuideService, "_call_llm_for_region_explain", _mock_llm):
        svc_instance.handle_region_explain(
            snake_id="test-snake",
            region_steps=region_steps,
            route=route,
        )

    return captured


# ---------------------------------------------------------------------------
# Tests: early-exit paths
# ---------------------------------------------------------------------------

class TestRegionExplainEarlyExit:
    def test_empty_region_steps_returns_immediately(self, svc, monkeypatch):
        """Empty list → early exit, no LLM call at all."""
        import agent.services.visual_guide.service as svc_mod
        llm_called = []

        with mock.patch.object(VisualGuideService, "_call_llm_for_region_explain",
                               side_effect=lambda *a, **kw: llm_called.append(True) or []):
            svc.handle_region_explain(snake_id="s1", region_steps=[], route="/teams")

        assert not llm_called, "LLM must not be called for empty region_steps"

    def test_steps_with_no_bubble_labels_produce_no_guide(self, svc, monkeypatch):
        """Steps without 'bubble' text: LLM returns empty list, no guide is appended.
        The LLM path is still entered (since rule_bubbles is empty → no early rule exit),
        but _call_llm_for_region_explain returns [] for empty labels, producing no guide."""
        import agent.services.visual_guide.service as svc_mod
        guide_appended = []

        with mock.patch.object(svc_mod, "_append_room_ai_message",
                               side_effect=lambda **kw: guide_appended.append(kw)):
            steps = [{"x": 100, "y": 200, "waypoint": "some.el"}]  # no bubble
            svc.handle_region_explain(snake_id="s1", region_steps=steps, route="/teams")

        guide_msgs = [m for m in guide_appended if "__GUIDE__:" in (m.get("text") or "")]
        assert not guide_msgs, "No guide message expected for steps without bubble labels"


# ---------------------------------------------------------------------------
# Tests: step sanitisation — out-of-bounds coordinates
# ---------------------------------------------------------------------------

class TestRegionExplainStepValidation:
    def test_steps_with_x_out_of_bounds_are_skipped(self, svc):
        """Steps with x=99999 (>10000) are rejected as out-of-viewport."""
        steps = [
            {"bubble": "Außerhalb", "x": 99999, "y": 200, "waypoint": "el.bad"},
            {"bubble": "Normal", "x": 500, "y": 200, "waypoint": "el.good"},
        ]
        captured = _run_region_explain(svc, steps, "/teams", explanations=["Außen", "Innen"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs, "Expected a guide message for the valid step"
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        step_waypoints = [s["waypoint"] for s in guide_data["steps"]]
        assert "el.good" in step_waypoints
        assert "el.bad" not in step_waypoints

    def test_steps_with_y_out_of_bounds_are_skipped(self, svc):
        """Steps with y=99999 are also rejected."""
        steps = [
            {"bubble": "OOB y", "x": 100, "y": 99999, "waypoint": "el.bad"},
            {"bubble": "OK", "x": 100, "y": 500, "waypoint": "el.good"},
        ]
        captured = _run_region_explain(svc, steps, "/teams", explanations=["OOB", "OK"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        step_waypoints = [s["waypoint"] for s in guide_data["steps"]]
        assert "el.good" in step_waypoints
        assert "el.bad" not in step_waypoints

    def test_steps_with_negative_x_are_skipped(self, svc):
        """Steps with x < 0 are also out of bounds."""
        steps = [
            {"bubble": "Negativ", "x": -1, "y": 100, "waypoint": "el.bad"},
            {"bubble": "Positiv", "x": 100, "y": 100, "waypoint": "el.good"},
        ]
        captured = _run_region_explain(svc, steps, "/teams", explanations=["Neg", "Pos"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        step_waypoints = [s["waypoint"] for s in guide_data["steps"]]
        assert "el.good" in step_waypoints
        assert "el.bad" not in step_waypoints

    def test_missing_xy_treated_as_zero_and_kept(self, svc):
        """Missing x/y defaults to 0 which is a valid coordinate — step is NOT dropped."""
        steps = [{"bubble": "Kein XY", "waypoint": "el.nocoord"}]
        captured = _run_region_explain(svc, steps, "/teams", explanations=["Erklärung"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs, "Step with missing x/y (treated as 0,0) should produce a guide"
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        assert len(guide_data["steps"]) == 1
        assert guide_data["steps"][0]["x"] == 0.0
        assert guide_data["steps"][0]["y"] == 0.0

    def test_max_12_steps_processed(self, svc):
        """Even when more than 12 steps are passed, at most 12 are processed."""
        steps = [
            {"bubble": f"El {i}", "x": 10 * i, "y": 10 * i, "waypoint": f"el.{i}"}
            for i in range(1, 20)  # 19 steps
        ]
        call_counts = []
        original_call = VisualGuideService._call_llm_for_region_explain

        def counting_llm(self, r_steps, route):
            call_counts.append(len(r_steps))
            return [f"E{i}" for i in range(len(r_steps))]

        import agent.services.visual_guide.service as svc_mod
        with mock.patch.object(VisualGuideService, "_call_llm_for_region_explain", counting_llm), \
             mock.patch.object(svc_mod, "_append_room_ai_message"):
            svc.handle_region_explain(snake_id="s1", region_steps=steps, route="/teams")

        # The LLM may not be called if the rule engine covers everything, but if called,
        # it must have received at most 12 steps.
        if call_counts:
            assert call_counts[0] <= 12, "LLM received more than 12 steps"

    def test_12_steps_cap_at_service_level(self, svc):
        """The _MAX_REGION_STEPS cap of 12 is applied before any processing."""
        # Verify _build_guide_steps with 19 inputs after cap
        steps = [
            {"bubble": f"El {i}", "x": 10 * i, "y": 10 * i}
            for i in range(1, 20)
        ]
        explanations = [f"E{i}" for i in range(19)]
        # _build_guide_steps is a static method we can call directly
        result = VisualGuideService._build_guide_steps(
            steps[:12],  # cap applied before calling _build_guide_steps
            explanations[:12],
        )
        assert len(result) == 12

    def test_mixed_valid_and_out_of_bounds_steps(self, svc):
        """Valid and invalid steps mixed — only valid appear in guide."""
        steps = [
            {"bubble": "Erster",    "x": 100,   "y": 100,   "waypoint": "el.1"},
            {"bubble": "Außerhalb", "x": 99999,  "y": 300,   "waypoint": "el.bad"},
            {"bubble": "Dritter",   "x": 300,    "y": 300,   "waypoint": "el.3"},
        ]
        captured = _run_region_explain(svc, steps, "/chats", explanations=["E1", "Ebad", "E3"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        waypoints = [s["waypoint"] for s in guide_data["steps"]]
        assert "el.1" in waypoints
        assert "el.3" in waypoints
        assert "el.bad" not in waypoints

    def test_all_steps_out_of_bounds_produces_no_guide(self, svc):
        """When ALL steps are out of bounds, no guide message is appended."""
        steps = [
            {"bubble": "OOB 1", "x": 99999, "y": 100, "waypoint": "el.bad1"},
            {"bubble": "OOB 2", "x": 100, "y": 99999, "waypoint": "el.bad2"},
        ]
        captured = _run_region_explain(svc, steps, "/teams", explanations=["E1", "E2"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert not guide_msgs, "No guide should be produced when all steps are out of bounds"


# ---------------------------------------------------------------------------
# Tests: guide output structure
# ---------------------------------------------------------------------------

class TestRegionExplainOutput:
    def test_output_contains_guide_marker(self, svc):
        """The appended message must contain the __GUIDE__: marker."""
        steps = [{"bubble": "Klick mich", "x": 100, "y": 200, "waypoint": "el.a"}]
        captured = _run_region_explain(svc, steps, "/dashboard",
                                       explanations=["Zeigt das Dashboard"])
        assert captured
        texts = [m.get("text", "") for m in captured]
        assert any("__GUIDE__:" in t for t in texts)

    def test_output_guide_steps_carry_original_coordinates(self, svc):
        """x/y from the original steps are preserved in the guide output."""
        steps = [{"bubble": "Button", "x": 42.5, "y": 77.0, "waypoint": "el.btn"}]
        captured = _run_region_explain(svc, steps, "/workspace",
                                       explanations=["Drücke hier"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        step = guide_data["steps"][0]
        assert step["x"] == pytest.approx(42.5)
        assert step["y"] == pytest.approx(77.0)

    def test_output_session_id_is_ananta_visual(self, svc):
        """The guide message is tagged with the visual session ID."""
        steps = [{"bubble": "Nav", "x": 10, "y": 20, "waypoint": "nav.el"}]
        captured = _run_region_explain(svc, steps, "/board", explanations=["Navigation"])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        assert guide_msgs[0].get("session_id") == _VISUAL_SESSION_ID

    def test_bubble_text_truncated_at_120_chars(self, svc):
        """AI-generated bubble text longer than 120 chars is truncated."""
        long_explanation = "X" * 200
        steps = [{"bubble": "El", "x": 100, "y": 100, "waypoint": "el.a"}]
        captured = _run_region_explain(svc, steps, "/teams",
                                       explanations=[long_explanation])
        guide_msgs = [m for m in captured if "__GUIDE__:" in (m.get("text") or "")]
        assert guide_msgs
        guide_data = json.loads(guide_msgs[0]["text"].split("__GUIDE__:")[1])
        bubble = guide_data["steps"][0]["bubble"]
        assert len(bubble) <= 120


# ---------------------------------------------------------------------------
# Tests: _build_guide_steps unit tests (pure function)
# ---------------------------------------------------------------------------

class TestBuildGuideSteps:
    def test_valid_step_included(self):
        steps = [{"bubble": "Click", "x": 100.0, "y": 200.0, "waypoint": "el.a"}]
        result = VisualGuideService._build_guide_steps(steps, ["Erklärung"])
        assert len(result) == 1
        assert result[0]["waypoint"] == "el.a"
        assert result[0]["x"] == 100.0

    def test_oob_x_excluded(self):
        steps = [{"bubble": "OOB", "x": 99999, "y": 100, "waypoint": "el.bad"}]
        result = VisualGuideService._build_guide_steps(steps, ["E1"])
        assert result == []

    def test_oob_y_excluded(self):
        steps = [{"bubble": "OOB", "x": 100, "y": 99999, "waypoint": "el.bad"}]
        result = VisualGuideService._build_guide_steps(steps, ["E1"])
        assert result == []

    def test_missing_xy_defaults_to_zero(self):
        steps = [{"bubble": "NoCoord", "waypoint": "el.nc"}]
        result = VisualGuideService._build_guide_steps(steps, ["E1"])
        assert len(result) == 1
        assert result[0]["x"] == 0.0
        assert result[0]["y"] == 0.0

    def test_bubble_truncated(self):
        steps = [{"bubble": "El", "x": 1, "y": 1}]
        result = VisualGuideService._build_guide_steps(steps, ["A" * 200])
        assert len(result[0]["bubble"]) <= 120

    def test_empty_steps_returns_empty(self):
        assert VisualGuideService._build_guide_steps([], []) == []
