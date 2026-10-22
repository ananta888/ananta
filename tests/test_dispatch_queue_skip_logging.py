"""Silent queue drops made a starvation untraceable; skipped candidates are logged."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from agent.services import task_queue_service as qs


class _Task:
    def __init__(self, task_id: str, status: str) -> None:
        self.id = task_id
        self.status = status
        self.team_id = None
        self.manual_override_until = 0

    def model_dump(self) -> dict:
        return {"id": self.id, "status": self.status}


def test_dispatch_queue_logs_why_candidates_are_skipped(monkeypatch, caplog):
    # Given one dispatchable task held back by an open recovery lease.
    task = _Task("t1", "todo")
    monkeypatch.setattr(qs, "task_repo", SimpleNamespace(get_all=lambda: [task]))
    monkeypatch.setattr(
        qs,
        "get_recovery_dispatch_gate_service",
        lambda: SimpleNamespace(
            evaluate_task=lambda t: SimpleNamespace(allowed=False, decision=SimpleNamespace(reason_code="lease_open"))
        ),
    )
    monkeypatch.setattr(
        qs,
        "get_organization_task_dispatch_gate_service",
        lambda: SimpleNamespace(evaluate=lambda t: SimpleNamespace(allowed=True, reason_code=None)),
    )
    monkeypatch.setattr(qs, "_last_skip_log_at", 0.0)
    # When the queue is asked for dispatchables.
    with caplog.at_level(logging.INFO):
        queue = qs.TaskQueueService().get_scoped_dispatch_queue()
    # Then nothing dispatches and the log names the blocking reason.
    assert queue == []
    assert "dispatch_candidates_skipped" in caplog.text
    assert "recovery_gate:lease_open" in caplog.text


def test_dispatch_queue_skips_are_throttled(monkeypatch, caplog):
    task = _Task("t2", "todo")
    monkeypatch.setattr(qs, "task_repo", SimpleNamespace(get_all=lambda: [task]))
    monkeypatch.setattr(
        qs,
        "get_recovery_dispatch_gate_service",
        lambda: SimpleNamespace(
            evaluate_task=lambda t: SimpleNamespace(allowed=False, decision=SimpleNamespace(reason_code="lease_open"))
        ),
    )
    monkeypatch.setattr(
        qs,
        "get_organization_task_dispatch_gate_service",
        lambda: SimpleNamespace(evaluate=lambda t: SimpleNamespace(allowed=True, reason_code=None)),
    )
    monkeypatch.setattr(qs, "_last_skip_log_at", 0.0)
    with caplog.at_level(logging.INFO):
        qs.TaskQueueService().get_scoped_dispatch_queue()
        qs.TaskQueueService().get_scoped_dispatch_queue()
    # The tick runs every second: at most one skip report per interval.
    assert caplog.text.count("dispatch_candidates_skipped") == 1
