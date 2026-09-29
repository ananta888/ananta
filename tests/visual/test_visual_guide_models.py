"""Tests for VisualGuide data models (VG-014).

Covers:
- VisualGuideRequest.to_dict() — snapshot clamped to 500 chars
- VisualGuideAction priority ordering
- VisualGuideTraceEvent serialization
"""
from __future__ import annotations

import time

import pytest

from agent.services.visual_guide.models import (
    VisualGuideAction,
    VisualGuideDecision,
    VisualGuideRequest,
    VisualGuideTraceEvent,
)


# ---------------------------------------------------------------------------
# VisualGuideRequest
# ---------------------------------------------------------------------------

class TestVisualGuideRequest:
    def test_to_dict_clamps_snapshot_to_500_chars(self):
        req = VisualGuideRequest(
            snake_id="snake-1",
            trigger_type="ui_tick",
            snapshot="x" * 1000,
        )
        d = req.to_dict()
        assert len(d["snapshot"]) == 500

    def test_to_dict_short_snapshot_unchanged(self):
        req = VisualGuideRequest(snapshot="hello")
        d = req.to_dict()
        assert d["snapshot"] == "hello"

    def test_to_dict_region_steps_count(self):
        req = VisualGuideRequest(
            trigger_type="region_explain",
            region_steps=[{"a": 1}, {"b": 2}],
        )
        d = req.to_dict()
        assert d["region_steps_count"] == 2

    def test_request_id_auto_generated(self):
        req1 = VisualGuideRequest()
        req2 = VisualGuideRequest()
        assert req1.request_id != req2.request_id

    def test_created_at_is_recent(self):
        before = time.time()
        req = VisualGuideRequest()
        after = time.time()
        assert before <= req.created_at <= after

    def test_to_dict_contains_required_keys(self):
        req = VisualGuideRequest(snake_id="s", trigger_type="ui_tick", route="/chats")
        d = req.to_dict()
        for key in ("request_id", "snake_id", "trigger_type", "route", "snapshot",
                    "region_steps_count", "created_at"):
            assert key in d


# ---------------------------------------------------------------------------
# VisualGuideDecision
# ---------------------------------------------------------------------------

class TestVisualGuideDecision:
    def test_to_dict_strategy_suppressed(self):
        dec = VisualGuideDecision(strategy="suppressed", reason="rate_limit")
        d = dec.to_dict()
        assert d["strategy"] == "suppressed"
        assert d["reason"] == "rate_limit"

    def test_to_dict_strategy_llm(self):
        dec = VisualGuideDecision(strategy="llm", confidence=0.8, model_used="gpt-4o-mini")
        d = dec.to_dict()
        assert d["confidence"] == 0.8
        assert d["model_used"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# VisualGuideAction — priority ordering
# ---------------------------------------------------------------------------

class TestVisualGuideAction:
    def test_default_priority_is_5(self):
        action = VisualGuideAction()
        assert action.priority == 5

    def test_region_explain_priority_lower_number_means_higher_priority(self):
        """Priority 2 (region_explain) is numerically less than 7 (predictive)."""
        region_action = VisualGuideAction(trigger_type="region_explain", priority=2)
        predictive_action = VisualGuideAction(trigger_type="ui_tick", priority=7)
        assert region_action.priority < predictive_action.priority

    def test_to_dict_contains_guide_steps(self):
        steps = [{"waypoint": "btn.ok", "bubble": "Click OK"}]
        action = VisualGuideAction(guide_steps=steps, priority=2)
        d = action.to_dict()
        assert d["guide_steps"] == steps
        assert d["priority"] == 2

    def test_to_dict_contains_all_keys(self):
        action = VisualGuideAction()
        d = action.to_dict()
        for key in ("request_id", "guide_steps", "trigger_type", "priority", "ttl_seconds", "created_at"):
            assert key in d

    def test_default_ttl_is_30_seconds(self):
        action = VisualGuideAction()
        assert action.ttl_seconds == 30.0

    def test_created_at_is_recent(self):
        before = time.time()
        action = VisualGuideAction()
        after = time.time()
        assert before <= action.created_at <= after

    def test_priority_sort_order(self):
        """Higher-priority actions (lower number) sort first."""
        actions = [
            VisualGuideAction(priority=7),
            VisualGuideAction(priority=2),
            VisualGuideAction(priority=5),
        ]
        sorted_actions = sorted(actions, key=lambda a: a.priority)
        assert sorted_actions[0].priority == 2
        assert sorted_actions[1].priority == 5
        assert sorted_actions[2].priority == 7


# ---------------------------------------------------------------------------
# VisualGuideTraceEvent — serialization
# ---------------------------------------------------------------------------

class TestVisualGuideTraceEvent:
    def test_to_dict_contains_all_keys(self):
        evt = VisualGuideTraceEvent(
            event="model_invoked",
            request_id="req-123",
            data={"strategy": "llm"},
        )
        d = evt.to_dict()
        assert d["event"] == "model_invoked"
        assert d["request_id"] == "req-123"
        assert d["data"] == {"strategy": "llm"}
        assert "ts" in d

    def test_ts_auto_set(self):
        before = time.time()
        evt = VisualGuideTraceEvent(event="test")
        after = time.time()
        assert before <= evt.ts <= after

    def test_empty_data_defaults_to_empty_dict(self):
        evt = VisualGuideTraceEvent(event="request_received")
        assert evt.data == {}

    def test_all_event_types_serialize(self):
        event_types = [
            "request_received", "snapshot_normalized", "delta_computed",
            "decision_started", "model_invoked", "action_generated",
            "action_sent", "fallback_used", "suppressed_by_rate_limit", "error",
        ]
        for evt_type in event_types:
            evt = VisualGuideTraceEvent(event=evt_type, request_id="r")
            d = evt.to_dict()
            assert d["event"] == evt_type
