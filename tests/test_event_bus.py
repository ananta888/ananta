"""Tests for HeuristicEventBus and event source adapters — event_bus.py."""
from __future__ import annotations

import time

import pytest

from agent.services.heuristic_runtime.event_bus import (
    EclipseEventSourceAdapter,
    HeuristicEvent,
    HeuristicEventBus,
    TuiEventSourceAdapter,
    get_event_bus,
)


# ── HeuristicEvent ────────────────────────────────────────────────────────────

def test_event_to_dict():
    ev = HeuristicEvent(event_type="focus_change", surface="tui_snake", normalized_value="editor")
    d = ev.to_dict()
    assert d["event_type"] == "focus_change"
    assert d["surface"] == "tui_snake"
    assert "event_id" in d
    assert "timestamp" in d


def test_event_normalized_value_truncated():
    ev = HeuristicEvent(event_type="focus_change", surface="tui_snake", normalized_value="x" * 300)
    assert len(ev.to_dict()["normalized_value"]) == 200


# ── HeuristicEventBus ─────────────────────────────────────────────────────────

def test_subscribe_and_receive():
    bus = HeuristicEventBus()
    received = []
    bus.subscribe(received.append)
    ev = HeuristicEvent(event_type="focus_change", surface="tui_snake")
    bus.publish(ev)
    assert len(received) == 1
    assert received[0] is ev


def test_unsubscribe_stops_delivery():
    bus = HeuristicEventBus()
    received = []
    bus.subscribe(received.append)
    bus.unsubscribe(received.append)
    bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake"))
    assert received == []


def test_duplicate_subscribe_ignored():
    bus = HeuristicEventBus()
    received = []
    bus.subscribe(received.append)
    bus.subscribe(received.append)
    bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake"))
    assert len(received) == 1


def test_subscriber_exception_does_not_propagate():
    bus = HeuristicEventBus()
    def bad_handler(ev): raise RuntimeError("boom")
    bus.subscribe(bad_handler)
    bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake"))  # must not raise


def test_get_recent_returns_newest_last():
    bus = HeuristicEventBus()
    for i in range(5):
        bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake", normalized_value=str(i)))
    recent = bus.get_recent(3)
    assert len(recent) == 3
    assert recent[-1].normalized_value == "4"


def test_ring_buffer_overflow_discards_oldest():
    bus = HeuristicEventBus(ring_buffer_size=3)
    for i in range(5):
        bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake", normalized_value=str(i)))
    all_events = bus.get_recent(10)
    assert len(all_events) == 3
    values = [e.normalized_value for e in all_events]
    assert "0" not in values
    assert "4" in values


def test_clear_empties_buffer():
    bus = HeuristicEventBus()
    bus.publish(HeuristicEvent(event_type="focus_change", surface="tui_snake"))
    bus.clear()
    assert bus.get_recent(10) == []


def test_subscriber_count():
    bus = HeuristicEventBus()
    assert bus.subscriber_count == 0
    bus.subscribe(lambda e: None)
    assert bus.subscriber_count == 1


# ── TuiEventSourceAdapter ─────────────────────────────────────────────────────

def test_tui_adapter_maps_cursor_move():
    adapter = TuiEventSourceAdapter()
    ev = adapter.adapt({"kind": "cursor_move", "value": "editor", "timestamp": 1.0})
    assert ev is not None
    assert ev.event_type == "pointer_move"
    assert ev.surface == "tui_snake"


def test_tui_adapter_maps_artifact_click():
    adapter = TuiEventSourceAdapter()
    ev = adapter.adapt({"kind": "artifact_click", "ref_id": "ref42", "value": "src/main.py"})
    assert ev.event_type == "artifact_select"
    assert ev.ref_id == "ref42"


def test_tui_adapter_maps_error_event():
    adapter = TuiEventSourceAdapter()
    ev = adapter.adapt({"kind": "error_event", "value": "NullPointerException"})
    assert ev.event_type == "error_detected"


def test_tui_adapter_passes_through_unknown_kind():
    adapter = TuiEventSourceAdapter()
    ev = adapter.adapt({"kind": "custom_kind", "value": "x"})
    assert ev is not None
    assert ev.event_type == "custom_kind"


def test_tui_adapter_returns_none_for_empty_kind():
    adapter = TuiEventSourceAdapter()
    result = adapter.adapt({"kind": "", "value": "x"})
    assert result is None


# ── EclipseEventSourceAdapter ─────────────────────────────────────────────────

def test_eclipse_adapter_maps_zone_change():
    adapter = EclipseEventSourceAdapter()
    ev = adapter.adapt({"snakeState": "ZONE_CHANGE", "zone": "editor", "timestamp": 1.0})
    assert ev is not None
    assert ev.event_type == "panel_switch"
    assert ev.surface == "eclipse_snake"


def test_eclipse_adapter_maps_error_state():
    adapter = EclipseEventSourceAdapter()
    ev = adapter.adapt({"snakeState": "ERROR", "zone": ""})
    assert ev.event_type == "error_detected"


def test_eclipse_adapter_normalized_value_is_zone():
    adapter = EclipseEventSourceAdapter()
    ev = adapter.adapt({"state": "FOLLOWING", "zone": "terminal"})
    assert ev.normalized_value == "terminal"


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_get_event_bus_returns_same_instance():
    b1 = get_event_bus()
    b2 = get_event_bus()
    assert b1 is b2
