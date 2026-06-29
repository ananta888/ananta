"""Tests for CaseFlow Timeline (CASECORE-004)."""
from __future__ import annotations

import time
import pytest
from datetime import datetime, timedelta
from agent.caseflow.timeline import CaseEvent, append_event, get_events_for_case, clear_events


@pytest.fixture(autouse=True)
def clean_timeline():
    clear_events()
    yield
    clear_events()


class TestCaseTimeline:
    def test_event_creation(self):
        evt = CaseEvent(case_id="c1", event_type="case_created", title="Case created")
        assert evt.case_id == "c1"
        assert evt.event_type == "case_created"
        assert evt.id is not None

    def test_event_has_case_id(self):
        evt = CaseEvent(case_id="my-case", event_type="status_changed", title="T")
        assert evt.case_id == "my-case"

    def test_events_are_ordered_by_created_at(self):
        t1 = datetime(2026, 1, 1, 10, 0, 0)
        t2 = datetime(2026, 1, 1, 10, 0, 1)
        t3 = datetime(2026, 1, 1, 10, 0, 2)
        e1 = CaseEvent(case_id="c", event_type="case_created", title="E1", created_at=t1)
        e2 = CaseEvent(case_id="c", event_type="artifact_added", title="E2", created_at=t3)
        e3 = CaseEvent(case_id="c", event_type="status_changed", title="E3", created_at=t2)
        for e in [e2, e1, e3]:
            append_event("c", e)
        events = get_events_for_case("c")
        assert events[0].title == "E1"
        assert events[1].title == "E3"
        assert events[2].title == "E2"

    def test_status_changed_event_has_from_to_in_payload(self):
        evt = CaseEvent(
            case_id="c",
            event_type="status_changed",
            title="Status: new → active",
            payload={"from_status": "new", "to_status": "active"},
        )
        append_event("c", evt)
        events = get_events_for_case("c")
        assert events[0].payload["from_status"] == "new"
        assert events[0].payload["to_status"] == "active"

    def test_trace_id_reference_in_event(self):
        evt = CaseEvent(
            case_id="c",
            event_type="agent_run_completed",
            title="Agent run done",
            trace_id="trace-abc-def",
            payload={"agent": "fit_evaluator"},
        )
        append_event("c", evt)
        events = get_events_for_case("c")
        assert events[0].trace_id == "trace-abc-def"
