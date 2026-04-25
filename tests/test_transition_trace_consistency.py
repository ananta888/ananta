from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "trace" / "transition_event.v1.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _golden_path_events() -> list[dict[str, Any]]:
    return [
        {
            "schema": "transition_event.v1",
            "event_id": "evt-001",
            "event_type": "goal_created",
            "timestamp": "2026-04-25T16:38:54+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-001",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "goal",
            "entity_id": "goal-1",
            "goal_id": "goal-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-002",
            "event_type": "plan_proposed",
            "timestamp": "2026-04-25T16:38:55+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-001",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "plan",
            "entity_id": "plan-1",
            "goal_id": "goal-1",
            "plan_id": "plan-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-003",
            "event_type": "task_created",
            "timestamp": "2026-04-25T16:38:56+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-002",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "task",
            "entity_id": "task-1",
            "goal_id": "goal-1",
            "plan_id": "plan-1",
            "task_id": "task-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-004",
            "event_type": "approval_requested",
            "timestamp": "2026-04-25T16:38:57+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-003",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "approval",
            "entity_id": "apr-1",
            "task_id": "task-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-005",
            "event_type": "approval_decided",
            "timestamp": "2026-04-25T16:38:58+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-004",
            "actor": {"component": "user", "id": "operator-1"},
            "state_type": "approval",
            "entity_id": "apr-1",
            "task_id": "task-1",
            "metadata": {"decision": "approved"},
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-006",
            "event_type": "execution_started",
            "timestamp": "2026-04-25T16:38:59+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-005",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "execution",
            "entity_id": "exec-1",
            "task_id": "task-1",
            "execution_id": "exec-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-007",
            "event_type": "execution_finished",
            "timestamp": "2026-04-25T16:39:00+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-006",
            "actor": {"component": "worker", "id": "worker-a"},
            "state_type": "execution",
            "entity_id": "exec-1",
            "task_id": "task-1",
            "execution_id": "exec-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-008",
            "event_type": "artifact_created",
            "timestamp": "2026-04-25T16:39:01+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-007",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "artifact",
            "entity_id": "artifact-1",
            "task_id": "task-1",
            "execution_id": "exec-1",
            "artifact_id": "artifact-1",
        },
        {
            "schema": "transition_event.v1",
            "event_id": "evt-009",
            "event_type": "verification_finished",
            "timestamp": "2026-04-25T16:39:02+02:00",
            "correlation_id": "corr-1",
            "causation_id": "evt-008",
            "actor": {"component": "hub", "id": "hub-main"},
            "state_type": "verification",
            "entity_id": "verify-1",
            "task_id": "task-1",
            "execution_id": "exec-1",
            "artifact_id": "artifact-1",
        },
    ]


def _validate_trace_consistency(events: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    known_event_ids: set[str] = set()
    execution_start_ids: set[str] = set()

    correlation_ids = {str(event.get("correlation_id") or "") for event in events}
    if "" in correlation_ids:
        problems.append("missing_correlation_id")
    if len(correlation_ids - {""}) > 1:
        problems.append("mixed_correlation_ids")

    for event in events:
        event_id = str(event.get("event_id") or "")
        if event_id in known_event_ids:
            problems.append(f"duplicate_event_id:{event_id}")
        known_event_ids.add(event_id)
        if str(event.get("event_type") or "") == "execution_started":
            execution_start_ids.add(str(event.get("execution_id") or ""))

    for event in events:
        event_id = str(event.get("event_id") or "")
        causation_id = str(event.get("causation_id") or "")
        if causation_id not in known_event_ids:
            problems.append(f"orphan_causation:{event_id}")

        event_type = str(event.get("event_type") or "")
        if event_type in {"execution_finished", "execution_failed"}:
            execution_id = str(event.get("execution_id") or "")
            if not execution_id:
                problems.append(f"missing_execution_id:{event_id}")
            elif execution_id not in execution_start_ids:
                problems.append(f"missing_execution_start_reference:{event_id}")
        if event_type == "artifact_created":
            if not str(event.get("task_id") or ""):
                problems.append(f"artifact_missing_task_id:{event_id}")
            if not str(event.get("execution_id") or ""):
                problems.append(f"artifact_missing_execution_id:{event_id}")

    return problems


def test_transition_event_schema_and_trace_consistency_accept_golden_path() -> None:
    events = _golden_path_events()
    validator = _validator()
    for event in events:
        assert list(validator.iter_errors(event)) == []
    assert _validate_trace_consistency(events) == []


def test_transition_trace_consistency_detects_orphans_and_execution_mismatches() -> None:
    events = _golden_path_events()
    events[6]["execution_id"] = "exec-other"
    events[8]["causation_id"] = "evt-missing"
    events[7]["task_id"] = ""

    problems = _validate_trace_consistency(events)
    assert "missing_execution_start_reference:evt-007" in problems
    assert "orphan_causation:evt-009" in problems
    assert "artifact_missing_task_id:evt-008" in problems


def test_transition_event_schema_rejects_unknown_event_type() -> None:
    event = _golden_path_events()[0]
    event["event_type"] = "unknown_event"

    errors = list(_validator().iter_errors(event))
    assert errors
