from __future__ import annotations

import pytest

from agent.services.agent_safety_state_store import (
    AgentSafetyStateConflictError,
    AgentSafetyStateStore,
)


def test_state_store_is_revisioned_durable_and_occ_guarded(tmp_path) -> None:
    path = tmp_path / "safety.sqlite3"
    store = AgentSafetyStateStore(path)
    first = store.append("run", "run-1", {"run_id": "run-1", "state": "active"}, expected_revision=0)
    second = store.append("run", "run-1", {**first, "state": "freeze"}, expected_revision=1)
    assert second["revision"] == 2
    assert AgentSafetyStateStore(path).get("run", "run-1")["state"] == "freeze"
    with pytest.raises(AgentSafetyStateConflictError, match="revision_conflict"):
        store.append("run", "run-1", {"run_id": "run-1"}, expected_revision=1)


def test_events_are_immutable_and_idempotent(tmp_path) -> None:
    store = AgentSafetyStateStore(tmp_path / "safety.sqlite3")
    event = {"event_id": "event-1", "run_id": "run-1", "event_digest": "a" * 64}
    assert store.append_event(event) == event
    assert store.append_event(event) == event
    with pytest.raises(AgentSafetyStateConflictError, match="idempotency_conflict"):
        store.append_event({**event, "event_digest": "b" * 64})


def test_list_returns_latest_revision_and_run_bound_events(tmp_path) -> None:
    store = AgentSafetyStateStore(tmp_path / "safety.sqlite3")
    first = store.append("incident_bundle", "bundle-1", {"run_id": "run-1", "state": "open"}, expected_revision=0)
    store.append("incident_bundle", "bundle-1", {**first, "state": "closed"}, expected_revision=1)
    store.append("incident_bundle", "bundle-2", {"run_id": "run-2", "state": "open"}, expected_revision=0)
    store.append_event({"event_id": "event-1", "run_id": "run-1", "event_digest": "a" * 64})
    store.append_event({"event_id": "event-2", "run_id": "run-2", "event_digest": "b" * 64})

    assert [item["state"] for item in store.list("incident_bundle", run_id="run-1")] == ["closed"]
    assert [item["event_id"] for item in store.list_events(run_id="run-1")] == ["event-1"]
