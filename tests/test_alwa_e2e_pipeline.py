"""ALWA-018: end-to-end approval-lifecycle pipeline tests.

The pipeline tested here is the full write→approval→decision→re-dispatch
flow exercised by the ananta worker tool loop:

  1. Worker encounters a mutation that needs approval, the tool loop
     puts the task into ``pending_approval`` and creates an
     ``ApprovalRequestDB`` via ``create_pending_request``.
  2. An operator (or auto-policy) decides granted|denied via
     ``decide_request``.
  3. On ``granted``, the task is re-dispatched (status=todo,
     status_reason_code=approval_granted_redispatch).
  4. The next worker attempt resolves the grant via
     ``resolve_grant_for_call`` and consumes it via
     ``consume_request``.

Three end-to-end scenarios are covered:

  * grant → re-dispatch → resolve → consume (happy path)
  * deny → task stays pending → second decide raises 409
  * digest mismatch → grant does not transfer to a different call

The tests build an in-memory SQLite engine, create the full
``SQLModel.metadata`` schema, and patch the module-level ``engine``
reference used by ``approval_request_service`` and the
``get_repository_registry`` so the production code never touches a
real database.
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine


# ---------------------------------------------------------------------------
# in-memory world fixture
# ---------------------------------------------------------------------------


class _FakeTask:
    """Minimal in-memory task with the fields the re-dispatch code touches."""

    def __init__(self, task_id: str, status: str) -> None:
        self.id = task_id
        self.status = status
        self.status_reason_code: str | None = None
        self.goal_id: str | None = None

    def save(self) -> None:
        """Real TaskDB.save() persists; the fake is a no-op marker."""
        return None


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


@pytest.fixture
def pipeline_world(monkeypatch: pytest.MonkeyPatch):
    """Wire the approval service to a fresh in-memory DB + fake task repo.

    Returns ``(svc, task_repo, audit_log)``.
    """
    # 1) fresh in-memory engine and full schema
    from agent import database
    from agent.db_models import (  # noqa: F401  -- import for metadata
        governance,  # noqa: F401
        tasks,  # noqa: F401
    )

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr("agent.services.approval_request_service._engine", lambda: test_engine)

    # 2) fake task repo (task re-dispatch reads via get_repository_registry)
    task_repo = _FakeTaskRepo()
    monkeypatch.setattr(
        "agent.services.repository_registry.get_repository_registry",
        lambda: _FakeRegistry(task_repo),
    )

    # 3) audit capture
    audit_log: list[tuple[str, dict[str, Any]]] = []

    def _capture(action: str, details: Any = None, *args: Any, **kwargs: Any) -> None:
        # ``log_audit(action, details, **kwargs)`` is called both as
        # positional and as keyword; normalize to a single dict.
        if details is None and "details" in kwargs:
            details = kwargs["details"]
        if not isinstance(details, dict):
            details = {"value": details}
        audit_log.append((action, details))

    monkeypatch.setattr("agent.common.audit.log_audit", _capture)

    # 4) bypass content-payload persistence (would need a real on-disk path)
    monkeypatch.setattr(
        "agent.services.approval_request_service.ApprovalRequestService._store_content_payload",
        lambda self, payload, content_hash: f"approval-payload:test:{content_hash[:12]}",
    )

    from agent.services.approval_request_service import ApprovalRequestService

    svc = ApprovalRequestService()
    return svc, task_repo, audit_log


# ---------------------------------------------------------------------------
# Test 1: grant → re-dispatch → resolve → consume (happy path)
# ---------------------------------------------------------------------------


def test_pipeline_grant_redispatches_and_unblocks(pipeline_world) -> None:
    """ALWA-018 E2E: a granted decision re-dispatches the task, the
    re-execution resolves the same grant, and consume moves it to
    status=consumed. The full audit chain must be present.
    """
    svc, task_repo, audit_log = pipeline_world

    # worker tool loop has already set the task to pending_approval
    task_repo.add(_FakeTask(task_id="task-e2e-1", status="pending_approval"))

    # 1) tool loop creates the pending request
    args_a = {"path": "src/main.py", "content": "print('hi')\n"}
    request = svc.create_pending_request(
        task_id="task-e2e-1",
        tool_name="repo.write_file",
        arguments=args_a,
        goal_id="goal-e2e-1",
        risk_class="controlled_workspace_writes",
    )
    assert request.status == "pending"
    assert request.arguments_digest
    assert request.content_artifact_ref is not None
    assert request.content_hash is not None

    # 2) operator grants
    granted = svc.decide_request(request.id, decision="granted", decided_by="operator")
    assert granted.status == "granted"

    # 3) re-dispatch happened
    task = task_repo.by_id["task-e2e-1"]
    assert task.status == "todo"
    assert task.status_reason_code == "approval_granted_redispatch"

    # 4) audit chain: created → decided → redispatch
    actions = [a for a, _ in audit_log]
    assert "approval_request_created" in actions
    assert "approval_request_decided" in actions
    assert "approval_request_redispatch" in actions

    # 5) re-execution resolves the grant
    resolved = svc.resolve_grant_for_call(
        tool_name="repo.write_file",
        arguments=args_a,
        task_id="task-e2e-1",
        goal_id="goal-e2e-1",
    )
    assert resolved is not None
    assert resolved.id == request.id

    # 6) consume moves it to consumed
    consumed = svc.consume_request(request.id)
    assert consumed is not None
    assert consumed.status == "consumed"
    actions = [a for a, _ in audit_log]
    assert "approval_request_consumed" in actions

    # 7) a second resolve must not return a consumed grant
    again = svc.resolve_grant_for_call(
        tool_name="repo.write_file",
        arguments=args_a,
        task_id="task-e2e-1",
        goal_id="goal-e2e-1",
    )
    assert again is None


# ---------------------------------------------------------------------------
# Test 2: deny → task stays blocked → second decide raises
# ---------------------------------------------------------------------------


def test_pipeline_deny_leaves_task_blocked(pipeline_world) -> None:
    """ALWA-018 E2E: a denied decision must NOT re-dispatch the task
    and a second decide attempt on the same request must raise
    ``ApprovalDecisionError(request_already_denied, 409)``.
    """
    from agent.services.approval_request_service import ApprovalDecisionError

    svc, task_repo, _audit_log = pipeline_world
    task_repo.add(_FakeTask(task_id="task-e2e-2", status="pending_approval"))

    request = svc.create_pending_request(
        task_id="task-e2e-2",
        tool_name="repo.write_file",
        arguments={"path": "src/x.py", "content": "x"},
        goal_id="goal-e2e-2",
    )

    denied = svc.decide_request(request.id, decision="denied", decided_by="operator")
    assert denied.status == "denied"

    # task must NOT be touched by a deny
    task = task_repo.by_id["task-e2e-2"]
    assert task.status == "pending_approval"
    assert task.status_reason_code is None
    assert task_repo.saves == []

    # resolve must return None
    resolved = svc.resolve_grant_for_call(
        tool_name="repo.write_file",
        arguments={"path": "src/x.py", "content": "x"},
        task_id="task-e2e-2",
        goal_id="goal-e2e-2",
    )
    assert resolved is None

    # second decide → 409
    with pytest.raises(ApprovalDecisionError) as exc_info:
        svc.decide_request(request.id, decision="granted", decided_by="operator")
    assert exc_info.value.code == "request_already_denied"
    assert exc_info.value.http_status == 409


# ---------------------------------------------------------------------------
# Test 3: digest mismatch → grant does not transfer to a different call
# ---------------------------------------------------------------------------


def test_pipeline_digest_mismatch_blocks_reuse(pipeline_world) -> None:
    """ALWA-018 E2E: a grant for call A (content X) MUST NOT resolve
    for call B (content Y) even if path/tool are identical. The audit
    log must show that B did not consume A's grant.
    """
    svc, _task_repo, audit_log = pipeline_world

    request = svc.create_pending_request(
        task_id="task-e2e-3",
        tool_name="repo.write_file",
        arguments={"path": "src/main.py", "content": "original"},
        goal_id="goal-e2e-3",
    )
    granted = svc.decide_request(request.id, decision="granted", decided_by="operator")
    assert granted.status == "granted"

    # same call → resolves
    resolved_same = svc.resolve_grant_for_call(
        tool_name="repo.write_file",
        arguments={"path": "src/main.py", "content": "original"},
        task_id="task-e2e-3",
        goal_id="goal-e2e-3",
    )
    assert resolved_same is not None
    assert resolved_same.id == request.id

    # different content → digest mismatch → no grant
    resolved_diff = svc.resolve_grant_for_call(
        tool_name="repo.write_file",
        arguments={"path": "src/main.py", "content": "tampered"},
        task_id="task-e2e-3",
        goal_id="goal-e2e-3",
    )
    assert resolved_diff is None

    # only the original request's digest should appear in decided audit,
    # and no consume event must have been emitted for the tampered call
    decided_details = [d for a, d in audit_log if a == "approval_request_decided"]
    assert len(decided_details) == 1
    assert "approval_request_consumed" not in [a for a, _ in audit_log]

    # consume the original
    consumed = svc.consume_request(request.id)
    assert consumed is not None
    assert consumed.status == "consumed"
