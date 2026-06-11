"""ALWA-008: tests for the post-grant re-dispatch logic.

The re-dispatch puts a pending_approval / blocked_pending_approval /
blocked task back into the dispatch flow (status="todo") after a
granted decision. It must NOT touch tasks that are already in
non-pending states, and it must log an approval_request_redispatch
audit event for every successful re-dispatch.
"""
from __future__ import annotations

import time
from typing import Any

import pytest


class _FakeTask:
    def __init__(self, task_id: str, status: str) -> None:
        self.id = task_id
        self.status = status
        self.status_reason_code: str | None = None
        self._saved: list[dict[str, Any]] = []

    def save(self, task: "_FakeTask | None" = None) -> None:
        target = task or self
        self._saved.append({"status": target.status, "reason": target.status_reason_code})


class _FakeTaskRepo:
    def __init__(self) -> None:
        self.by_id: dict[str, _FakeTask] = {}
        self.saves: list[_FakeTask] = []

    def add(self, task: _FakeTask) -> None:
        self.by_id[task.id] = task

    def get_by_id(self, task_id: str) -> _FakeTask | None:
        return self.by_id.get(task_id)

    def save(self, task: _FakeTask) -> None:
        task.save()
        self.saves.append(task)


class _FakeRegistry:
    def __init__(self, repo: _FakeTaskRepo) -> None:
        self.task_repo = repo


def test_redispatch_moves_pending_approval_task_to_todo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ALWA-008: a granted decision re-dispatches the task by setting
    status=todo and recording status_reason_code=approval_granted_redispatch.
    """
    from agent.services.approval_request_service import (
        ApprovalRequestDB,
        ApprovalRequestService,
        AUDIT_APPROVAL_REQUEST_REDISPATCH,
    )

    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.common.audit.log_audit",
        lambda action, details: captured.append((action, details)),
    )

    repo = _FakeTaskRepo()
    repo.add(_FakeTask("task-rd-1", "pending_approval"))
    monkeypatch.setattr(
        "agent.services.repository_registry.get_repository_registry",
        lambda: _FakeRegistry(repo),
    )

    svc = ApprovalRequestService()
    request = ApprovalRequestDB(
        id="req-rd-1",
        task_id="task-rd-1",
        goal_id="goal-rd-1",
        tool_name="repo.write_file",
        arguments_digest="abc",
        status="granted",
        created_at=time.time(),
        expires_at=time.time() + 600,
    )
    svc._redispatch_task_after_grant(request)

    task = repo.by_id["task-rd-1"]
    assert task.status == "todo"
    assert task.status_reason_code == "approval_granted_redispatch"
    actions = [a for a, _ in captured]
    assert AUDIT_APPROVAL_REQUEST_REDISPATCH in actions


def test_redispatch_skips_already_running_task() -> None:
    """ALWA-008: a task that is not in a pending state must NOT be
    touched (no save, no audit row).
    """
    from agent.services.approval_request_service import (
        ApprovalRequestDB,
        ApprovalRequestService,
    )

    repo = _FakeTaskRepo()
    repo.add(_FakeTask("task-rd-2", "running"))
    import unittest.mock as mock
    with mock.patch(
        "agent.services.repository_registry.get_repository_registry",
        return_value=_FakeRegistry(repo),
    ):
        with mock.patch("agent.common.audit.log_audit") as audit:
            svc = ApprovalRequestService()
            request = ApprovalRequestDB(
                id="req-rd-2",
                task_id="task-rd-2",
                tool_name="repo.write_file",
                arguments_digest="abc",
                status="granted",
                created_at=time.time(),
                expires_at=time.time() + 600,
            )
            svc._redispatch_task_after_grant(request)

    task = repo.by_id["task-rd-2"]
    assert task.status == "running"  # unchanged
    assert task.status_reason_code is None
    assert repo.saves == []
    audit.assert_not_called()


def test_redispatch_handles_missing_task_quietly() -> None:
    """ALWA-008: a missing task must not raise; the dispatch loop
    must continue.
    """
    from agent.services.approval_request_service import (
        ApprovalRequestDB,
        ApprovalRequestService,
    )

    repo = _FakeTaskRepo()  # empty
    import unittest.mock as mock
    with mock.patch(
        "agent.services.repository_registry.get_repository_registry",
        return_value=_FakeRegistry(repo),
    ):
        with mock.patch("agent.common.audit.log_audit") as audit:
            svc = ApprovalRequestService()
            request = ApprovalRequestDB(
                id="req-rd-3",
                task_id="task-missing",
                tool_name="repo.write_file",
                arguments_digest="abc",
                status="granted",
                created_at=time.time(),
                expires_at=time.time() + 600,
            )
            # Must not raise.
            svc._redispatch_task_after_grant(request)
    audit.assert_not_called()
