"""Exactly-once terminal trace projection over durable binding state.

The acceptance contract: a terminal run stays pending until a projection
acknowledges its exact revision; a crash mid-projection retries rather than
losing the trace; a restart resumes from durable state; and two reconcilers
racing the same run project it once, never twice.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from agent.db_models.workflow_runtime import WorkflowControlBindingDB
from agent.services.workflow_terminal_trace_reconciliation import (
    SQLAlchemyWorkflowTerminalTraceStateStore,
    TerminalTraceCandidate,
    WorkflowTerminalTraceError,
    WorkflowTerminalTraceReconciler,
    WorkflowTerminalTraceRuntime,
    build_workflow_terminal_trace_runtime,
    is_terminal_status,
    status_revision,
)

_NOW = 1_000.0


def _engine() -> Any:
    engine = create_engine("sqlite://")
    WorkflowControlBindingDB.metadata.create_all(engine, tables=[WorkflowControlBindingDB.__table__])
    return engine


def _seed(engine: Any, *, workflow_id: str = "workflow-a", revision: int = 1) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.insert(WorkflowControlBindingDB).values(
                id=workflow_id,
                tenant_id="tenant-a",
                subject_id="subject-a",
                workflow_id=workflow_id,
                run_id=f"run-{workflow_id}",
                runtime_id="ananta-native",
                plan_hash="f" * 64,
                policy_version="policy-v1",
                checkpoint_id="checkpoint-7",
                workflow_request={},
                execution_plan={},
                last_status={},
                public_status={},
                runtime_revision=revision,
                runtime_checkpoint_ref="checkpoint-7",
                revision=revision,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )


def _store(engine: Any) -> SQLAlchemyWorkflowTerminalTraceStateStore:
    return SQLAlchemyWorkflowTerminalTraceStateStore(engine, clock=lambda: _NOW)


def _events(count: int, *, start: int = 1) -> list[dict[str, Any]]:
    return [
        {"event_id": f"wfe-{index:06d}", "sequence": index, "event_type": "workflow.step.completed"}
        for index in range(start, start + count)
    ]


class _Projection:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.pages: list[tuple[str, tuple[dict[str, Any], ...]]] = []
        self.fail_times = fail_times

    def __call__(self, candidate: TerminalTraceCandidate, events: tuple[dict[str, Any], ...]) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("projection sink unavailable")
        self.pages.append((candidate.workflow_id, events))

    @property
    def projected_ids(self) -> list[str]:
        return [str(event["event_id"]) for _workflow, page in self.pages for event in page]


def _history(all_events: list[dict[str, Any]]) -> Any:
    def read(candidate: TerminalTraceCandidate, cursor: str) -> list[dict[str, Any]]:
        del candidate
        if not cursor:
            return list(all_events)
        anchor = next((index for index, event in enumerate(all_events) if str(event["sequence"]) == cursor), None)
        if anchor is None:
            raise LookupError("unknown cursor")
        return list(all_events[anchor + 1 :])

    return read


def test_a_terminal_run_is_projected_once_and_then_no_longer_pending() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    projection = _Projection()
    reconciler = WorkflowTerminalTraceReconciler(
        state=store,
        history=_history(_events(3)),
        project=projection,
    )

    first = reconciler.drain()
    second = reconciler.drain()

    assert first.projected == 1
    assert second.projected == 0
    assert projection.projected_ids == ["wfe-000001", "wfe-000002", "wfe-000003"]
    assert store.list_pending(limit=8) == ()


def test_a_failed_projection_keeps_the_trace_pending_for_the_next_pass() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    projection = _Projection(fail_times=1)
    reconciler = WorkflowTerminalTraceReconciler(
        state=store,
        history=_history(_events(2)),
        project=projection,
    )

    failed = reconciler.drain()

    assert failed.projected == 0
    assert failed.failed == ("workflow-a",)
    assert len(store.list_pending(limit=8)) == 1

    recovered = reconciler.drain()

    assert recovered.projected == 1
    assert projection.projected_ids == ["wfe-000001", "wfe-000002"]


def test_pending_state_survives_a_restart_because_it_lives_in_the_database() -> None:
    engine = _engine()
    _seed(engine)
    _store(engine).mark_pending("workflow-a", revision=8)

    # A fresh store object stands in for a restarted process.
    restarted = _store(engine)

    pending = restarted.list_pending(limit=8)
    assert [candidate.workflow_id for candidate in pending] == ["workflow-a"]
    assert pending[0].pending_revision == 8


def test_two_concurrent_reconcilers_acknowledge_the_run_exactly_once() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    candidate = store.list_pending(limit=8)[0]

    first = store.acknowledge("workflow-a", revision=candidate.pending_revision, cursor="3")
    second = store.acknowledge("workflow-a", revision=candidate.pending_revision, cursor="3")

    assert first is True
    assert second is False


def test_a_racing_reconciler_reports_a_deferral_rather_than_a_second_projection() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)

    class _RacedStore:
        """Hands out the candidate but lets a rival win the acknowledgement."""

        def mark_pending(self, workflow_id: str, *, revision: int) -> None:
            store.mark_pending(workflow_id, revision=revision)

        def list_pending(self, *, limit: int) -> tuple[TerminalTraceCandidate, ...]:
            return store.list_pending(limit=limit)

        def acknowledge(self, workflow_id: str, *, revision: int, cursor: str) -> bool:
            store.acknowledge(workflow_id, revision=revision, cursor=cursor)
            return False

    projection = _Projection()
    reconciler = WorkflowTerminalTraceReconciler(
        state=_RacedStore(),
        history=_history(_events(2)),
        project=projection,
    )

    report = reconciler.drain()

    assert report.projected == 0
    assert report.deferred == 1
    assert store.list_pending(limit=8) == ()


def test_a_long_history_is_projected_in_bounded_pages_covering_it_once() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    history = _events(600)
    projection = _Projection()
    reconciler = WorkflowTerminalTraceReconciler(
        state=store,
        history=_history(history),
        project=projection,
        page_size=256,
    )

    report = reconciler.drain()

    assert report.projected == 1
    assert [len(page) for _workflow, page in projection.pages] == [256, 256, 88]
    assert projection.projected_ids == [str(event["event_id"]) for event in history]


def test_an_older_terminal_observation_never_reopens_a_projected_trace() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    store.acknowledge("workflow-a", revision=8, cursor="8")

    store.mark_pending("workflow-a", revision=5)

    assert store.list_pending(limit=8) == ()


def test_a_newer_terminal_revision_reopens_the_trace() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    store.mark_pending("workflow-a", revision=8)
    store.acknowledge("workflow-a", revision=8, cursor="8")

    store.mark_pending("workflow-a", revision=12)

    pending = store.list_pending(limit=8)
    assert [candidate.pending_revision for candidate in pending] == [12]


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ({"status": "completed"}, True),
        ({"status": "failed"}, True),
        ({"status": "cancelled"}, True),
        ({"status": "timed_out"}, True),
        ({"status": "running"}, False),
        ({}, False),
        ({"status": 7}, False),
    ),
)
def test_terminal_status_detection_covers_every_terminal_state(
    status: dict[str, Any],
    expected: bool,
) -> None:
    assert is_terminal_status(status) is expected


@pytest.mark.parametrize("value", ({}, {"revision": -1}, {"revision": True}, {"revision": "8"}))
def test_a_status_without_a_usable_revision_is_rejected(value: dict[str, Any]) -> None:
    with pytest.raises(WorkflowTerminalTraceError, match="status_revision_invalid"):
        status_revision(value)


@pytest.mark.parametrize("page_size", (0, -1, 257, True))
def test_an_out_of_range_page_size_is_rejected(page_size: Any) -> None:
    engine = _engine()
    with pytest.raises(WorkflowTerminalTraceError, match="page_size_invalid"):
        WorkflowTerminalTraceReconciler(
            state=_store(engine),
            history=_history([]),
            project=_Projection(),
            page_size=page_size,
        )


def test_drain_report_is_countable_for_a_reconcile_summary() -> None:
    engine = _engine()
    _seed(engine)
    store = _store(engine)
    reconciler = WorkflowTerminalTraceReconciler(
        state=store,
        history=_history([]),
        project=_Projection(),
    )

    assert reconciler.drain().to_dict() == {"projected": 0, "deferred": 0, "failed": []}

def test_the_runtime_builder_assembles_a_usable_trace_path() -> None:
    """Enabling the safe half of the cutover must be one decision, not two."""

    engine = _engine()
    _seed(engine)
    runtime = build_workflow_terminal_trace_runtime(
        engine,
        history=_history(_events(2)),
        project=_Projection(),
        clock=lambda: _NOW,
    )

    runtime.state.mark_pending("workflow-a", revision=8)
    report = runtime.reconciler.drain()

    assert report.projected == 1
    assert runtime.state.list_pending(limit=8) == ()


def test_a_half_configured_trace_runtime_is_refused() -> None:
    engine = _engine()
    store = _store(engine)

    with pytest.raises(WorkflowTerminalTraceError, match="reconciler_invalid"):
        WorkflowTerminalTraceRuntime(state=store, reconciler=object())  # type: ignore[arg-type]
    with pytest.raises(WorkflowTerminalTraceError, match="state_invalid"):
        WorkflowTerminalTraceRuntime(
            state=object(),  # type: ignore[arg-type]
            reconciler=WorkflowTerminalTraceReconciler(
                state=store,
                history=_history([]),
                project=_Projection(),
            ),
        )

