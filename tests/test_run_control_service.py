"""RC-130: Tests for Run-Control Service and API.

Tests cover:
  - RunCommand dispatch (all 8 types)
  - Idempotency-key prevents duplicate execution
  - Policy rejection for unknown command types
  - Instruction-Injection persistence, supersede, applied
  - Branch creation, selection, pausing of alternatives
  - Approval gate shims (approve/deny)
  - Control-state read model aggregation
  - Audit events fired (non-fatal on failure)
  - TaskAdminService wiring (pause/resume/cancel/retry)
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event, RLock
from unittest.mock import MagicMock, patch

import pytest

from agent.auth import generate_token
from agent.config import settings
from agent.services.run_control_service import (
    COMMAND_TYPES,
    RunCommandIdempotencyConflictError,
    RunControlAuthorizationError,
    RunControlPrincipal,
    RunControlService,
    get_run_control_service,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def svc() -> RunControlService:
    """Fresh RunControlService for each test."""
    return RunControlService()


def _mock_intervene(*, ok: bool = True, msg: str = "ok", data: dict | None = None):
    """Patch TaskAdminService.intervene_task via service_registry module."""
    return patch(
        "agent.services.service_registry.get_core_services",
        return_value=MagicMock(
            task_admin_service=MagicMock(
                intervene_task=MagicMock(return_value=(ok, msg, data or {"id": "t1", "status": "paused"}))
            )
        ),
    )


def _mock_approval_decide(*, raises=None, result_status="granted"):
    """Patch ApprovalRequestService.decide_request via approval_request_service module."""
    if raises:
        mock = MagicMock(side_effect=raises)
    else:
        row = MagicMock(status=result_status)
        mock = MagicMock(return_value=row)
    return patch(
        "agent.services.approval_request_service.get_approval_request_service",
        return_value=MagicMock(
            get_request=MagicMock(
                return_value=MagicMock(task_id="t1", goal_id=None),
            ),
            decide_request=mock,
        ),
    )


# ── COMMAND_TYPES completeness ─────────────────────────────────────────────────

def test_all_command_types_defined():
    expected = {
        "pause_run", "resume_run", "cancel_run", "retry_run_or_task",
        "inject_instruction", "select_branch", "approve_gate", "deny_gate",
    }
    assert COMMAND_TYPES == expected


# ── Unknown command type ───────────────────────────────────────────────────────

def test_unknown_command_type_rejected(svc):
    cmd = svc.send_command(command_type="fly_to_moon", task_id="t1")
    assert cmd.status == "rejected_by_policy"
    assert "unknown_command_type" in cmd.result.get("error", "")
    assert "allowed" in cmd.result


# ── Pause/Resume/Cancel/Retry ─────────────────────────────────────────────────

def test_pause_no_task_id(svc):
    cmd = svc.send_command(command_type="pause_run")
    assert cmd.status == "rejected_by_policy"
    assert "task_id_required" in cmd.result["error"]


def test_pause_success(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd = svc.send_command(command_type="pause_run", task_id="t1", requested_by="operator")
    assert cmd.status == "applied"
    assert cmd.task_id == "t1"
    assert cmd.effective_at is not None


def test_pause_invalid_transition(svc):
    with _mock_intervene(ok=False, msg="invalid_transition", data={"current_status": "paused"}):
        cmd = svc.send_command(command_type="pause_run", task_id="t1")
    assert cmd.status == "rejected_by_policy"
    assert cmd.result["error"] == "invalid_transition"


def test_cancel_success(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "cancelled"}):
        cmd = svc.send_command(command_type="cancel_run", task_id="t1")
    assert cmd.status == "applied"


def test_retry_success(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "todo"}):
        cmd = svc.send_command(command_type="retry_run_or_task", task_id="t1")
    assert cmd.status == "applied"


def test_resume_without_instruction(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "todo"}):
        cmd = svc.send_command(command_type="resume_run", task_id="t1")
    assert cmd.status == "applied"
    assert "instruction_id" not in cmd.result


def test_resume_with_instruction_persists(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "todo"}):
        cmd = svc.send_command(
            command_type="resume_run",
            task_id="t1",
            payload={"instruction": "Keine React-Lösung", "mode": "next_iteration_instruction"},
        )
    assert cmd.status == "applied"
    assert "instruction_id" in cmd.result
    active = svc.get_active_instruction(task_id="t1")
    assert active is not None
    assert active.text == "Keine React-Lösung"


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_idempotency_key_prevents_duplicate(svc):
    payload = {
        "runtime_operations_governance": {
            "approval_id": "approval-1",
            "evidence_refs": ["ev-1"],
        }
    }
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}) as core_services:
        cmd1 = svc.send_command(
            command_type="pause_run",
            task_id="t1",
            run_id="run-1",
            requested_by="operator-a",
            payload=payload,
            idempotency_key="op:t1:pause:1",
        )
        cmd2 = svc.send_command(
            command_type="pause_run",
            task_id="t1",
            run_id="run-1",
            requested_by="operator-a",
            payload=deepcopy(payload),
            idempotency_key="op:t1:pause:1",
        )
    assert cmd1.command_id == cmd2.command_id
    # Only one entry in _commands despite two calls
    assert len(svc._commands) == 1
    assert core_services.return_value.task_admin_service.intervene_task.call_count == 1


def test_concurrent_idempotent_replay_is_reserved_before_the_side_effect(svc):
    side_effect_entered = Event()
    release_side_effect = Event()

    def intervene_task(**_kwargs):
        side_effect_entered.set()
        assert release_side_effect.wait(timeout=2.0)
        return True, "ok", {"id": "t1", "status": "paused"}

    core_services = MagicMock(
        task_admin_service=MagicMock(
            intervene_task=MagicMock(side_effect=intervene_task),
        )
    )
    request = {
        "command_type": "pause_run",
        "task_id": "t1",
        "run_id": "run-1",
        "requested_by": "operator-a",
        "idempotency_key": "concurrent-operation-key",
    }
    with (
        patch(
            "agent.services.service_registry.get_core_services",
            return_value=core_services,
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(svc.send_command, **request)
        assert side_effect_entered.wait(timeout=2.0)
        replay = pool.submit(svc.send_command, **request).result(timeout=2.0)
        # Contract: an exact concurrent replay is the already-reserved command
        # and may therefore be an explicit in-flight ``accepted`` snapshot.
        assert replay.status == "accepted"
        assert replay.result == {}
        release_side_effect.set()
        original = first.result(timeout=2.0)

    assert replay.command_id == original.command_id
    assert core_services.task_admin_service.intervene_task.call_count == 1
    assert len(svc._commands) == 1


@pytest.mark.parametrize(
    ("changed_field", "changed_value", "expected_mismatch"),
    [
        ("command_type", "cancel_run", "command_type"),
        ("task_id", "task-2", "task_id"),
        ("goal_id", "goal-2", "goal_id"),
        ("run_id", "run-2", "run_id"),
        ("approval_id", "approval-2", "payload"),
        ("evidence_refs", ["ev-2"], "payload"),
        ("read_model_sequence", 8, "payload"),
    ],
)
def test_idempotency_key_reuse_with_changed_request_is_auditable_conflict(
    svc,
    changed_field,
    changed_value,
    expected_mismatch,
):
    first_request = {
        "command_type": "pause_run",
        "task_id": "task-1",
        "goal_id": "goal-1",
        "run_id": "run-1",
        "requested_by": "operator-a",
        "payload": {
            "runtime_operations_governance": {
                "approval_id": "approval-1",
                "evidence_refs": ["ev-1"],
                "read_model_sequence": 7,
            }
        },
        "idempotency_key": "operation-command-key",
    }
    replay_request = deepcopy(first_request)
    if changed_field in {"approval_id", "evidence_refs", "read_model_sequence"}:
        replay_request["payload"]["runtime_operations_governance"][changed_field] = changed_value
    else:
        replay_request[changed_field] = changed_value

    class _TrackingLock:
        def __init__(self) -> None:
            self._lock = RLock()
            self.held = False

        def __enter__(self):
            self._lock.acquire()
            self.held = True
            return self

        def __exit__(self, *_args):
            self.held = False
            self._lock.release()

    tracking_lock = _TrackingLock()
    svc._command_lock = tracking_lock

    def record_audit(*_args, **_kwargs):
        assert tracking_lock.held is False

    with (
        _mock_intervene(ok=True, data={"id": "task-1", "status": "paused"}) as core_services,
        patch(
            "agent.services.run_control_service.log_audit",
            side_effect=record_audit,
        ) as audit,
    ):
        original = svc.send_command(**first_request)
        with pytest.raises(RunCommandIdempotencyConflictError) as conflict_info:
            svc.send_command(**replay_request)

    conflict = conflict_info.value
    assert conflict.reason_code == "run_command_idempotency_conflict"
    assert conflict.existing_command_id == original.command_id
    assert conflict.idempotency_key_ref.startswith("idempotency-sha256:")
    assert first_request["idempotency_key"] not in conflict.idempotency_key_ref
    assert expected_mismatch in conflict.mismatched_fields
    assert len(svc._commands) == 1
    assert core_services.return_value.task_admin_service.intervene_task.call_count == 1
    conflict_audits = [
        call.args[1]
        for call in audit.call_args_list
        if call.args[0] == "run_command_idempotency_conflict"
    ]
    assert len(conflict_audits) == 1
    assert expected_mismatch in conflict_audits[0]["mismatched_fields"]
    assert "payload" not in conflict_audits[0]
    assert "idempotency_key" not in conflict_audits[0]
    assert conflict_audits[0]["idempotency_key_ref"] == conflict.idempotency_key_ref
    assert all(
        first_request["idempotency_key"] not in repr(call.args[1])
        for call in audit.call_args_list
    )


def test_different_idempotency_keys_create_separate_commands(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd1 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="k1")
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd2 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="k2")
    assert cmd1.command_id != cmd2.command_id


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/runs/run-idempotency-route/commands",
        "/api/tasks/task-idempotency-route/commands",
        "/api/goals/goal-idempotency-route/commands",
    ],
)
def test_run_control_routes_return_stable_409_without_duplicate_effect(
    client,
    monkeypatch: pytest.MonkeyPatch,
    svc,
    endpoint,
):
    monkeypatch.setattr(
        "agent.routes.run_control.get_run_control_service",
        lambda: svc,
    )
    monkeypatch.setattr("agent.services.run_control_service.log_audit", lambda *_args: None)
    token = generate_token(
        {"sub": "route-operator", "role": "user"},
        settings.secret_key,
    )
    headers = {"Authorization": f"Bearer {token}"}
    principal = RunControlPrincipal.from_values("route-operator", "route-operator")
    if "/api/runs/" in endpoint:
        assert svc.bind_resource_owners(
            principal=principal,
            resources=(("task", "run-idempotency-route"), ("run", "run-idempotency-route")),
        )
    elif "/api/tasks/" in endpoint:
        assert svc.bind_resource_owner(
            kind="task",
            resource_id="task-idempotency-route",
            principal=principal,
        )
    else:
        assert svc.bind_resource_owner(
            kind="goal",
            resource_id="goal-idempotency-route",
            principal=principal,
        )
    request_body = {
        "type": "inject_instruction",
        "idempotency_key": "raw-route-idempotency-key",
        "payload": {"text": "Apply this once"},
    }

    accepted = client.post(endpoint, headers=headers, json=request_body)
    replayed = client.post(endpoint, headers=headers, json=request_body)
    conflict = client.post(
        endpoint,
        headers=headers,
        json={
            **request_body,
            "payload": {"text": "This must not be applied"},
        },
    )

    assert accepted.status_code == 200
    assert replayed.status_code == 200
    assert (
        accepted.get_json()["command"]["command_id"]
        == replayed.get_json()["command"]["command_id"]
    )
    assert conflict.status_code == 409
    assert conflict.get_json() == {
        "status": "error",
        "reason_code": "runtime_command_idempotency_conflict",
    }
    assert len(svc._commands) == 1
    assert len(svc._instructions) == 1


def test_idempotency_and_resources_are_exactly_principal_scoped(svc):
    owner = RunControlPrincipal.from_values("tenant-a", "operator")
    intruder = RunControlPrincipal.from_values("tenant-b", "operator")
    assert svc.bind_resource_owner(kind="task", resource_id="task-a", principal=owner)
    assert svc.bind_resource_owner(kind="task", resource_id="task-b", principal=intruder)

    first = svc.send_command(
        command_type="inject_instruction",
        task_id="task-a",
        payload={"text": "owner instruction"},
        requested_by=owner.subject_id,
        tenant_id=owner.tenant_id,
        subject_id=owner.subject_id,
        idempotency_key="same-client-key",
    )
    independent = svc.send_command(
        command_type="inject_instruction",
        task_id="task-b",
        payload={"text": "tenant-b instruction"},
        requested_by=intruder.subject_id,
        tenant_id=intruder.tenant_id,
        subject_id=intruder.subject_id,
        idempotency_key="same-client-key",
    )
    with pytest.raises(RunControlAuthorizationError):
        svc.send_command(
            command_type="inject_instruction",
            task_id="task-a",
            payload={"text": "must not run"},
            requested_by=intruder.subject_id,
            tenant_id=intruder.tenant_id,
            subject_id=intruder.subject_id,
            idempotency_key="same-client-key",
        )

    assert first.command_id != independent.command_id
    assert len(svc._instructions) == 2


def test_legacy_task_binding_is_deterministic_not_first_tenant_wins(svc):
    task = MagicMock(
        history=[{"event_type": "task_ingested", "actor": "shared-subject"}],
    )
    repositories = MagicMock()
    repositories.task_repo.get_by_id.return_value = task
    foreign = RunControlPrincipal.from_values("tenant-a", "shared-subject")
    legacy_owner = RunControlPrincipal.from_values("shared-subject", "shared-subject")

    with patch(
        "agent.services.repository_registry.get_repository_registry",
        return_value=repositories,
    ):
        assert not svc.authorize_resources(
            principal=foreign,
            task_id="legacy-task",
        )
        assert ("task", "legacy-task") not in svc._resource_owners
        assert svc.authorize_resources(
            principal=legacy_owner,
            task_id="legacy-task",
        )

    assert svc._resource_owners[("task", "legacy-task")] == legacy_owner


def test_run_control_route_hides_foreign_resource(client, monkeypatch, svc):
    monkeypatch.setattr("agent.routes.run_control.get_run_control_service", lambda: svc)
    owner = RunControlPrincipal.from_values("tenant-a", "shared-subject")
    assert svc.bind_resource_owner(kind="task", resource_id="private-task", principal=owner)
    owner_token = generate_token(
        {"sub": "shared-subject", "tenant_id": "tenant-a", "role": "user"},
        settings.secret_key,
    )
    foreign_token = generate_token(
        {"sub": "shared-subject", "tenant_id": "tenant-b", "role": "user"},
        settings.secret_key,
    )

    denied_read = client.get(
        "/api/tasks/private-task/control-state",
        headers={"Authorization": f"Bearer {foreign_token}"},
    )
    denied_write = client.post(
        "/api/tasks/private-task/commands",
        headers={"Authorization": f"Bearer {foreign_token}"},
        json={"type": "inject_instruction", "payload": {"text": "foreign"}},
    )
    allowed = client.post(
        "/api/tasks/private-task/commands",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"type": "inject_instruction", "payload": {"text": "owner"}},
    )

    assert denied_read.status_code == denied_write.status_code == 404
    assert denied_read.get_json() == denied_write.get_json()
    assert allowed.status_code == 200


# ── Instruction injection ─────────────────────────────────────────────────────

def test_inject_instruction_empty_text(svc):
    cmd = svc.send_command(command_type="inject_instruction", task_id="t1", payload={"text": "  "})
    assert cmd.status == "rejected_by_policy"
    assert "instruction_text_required" in cmd.result["error"]


def test_inject_instruction_too_long(svc):
    cmd = svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "x" * 4001},
    )
    assert cmd.status == "rejected_by_policy"
    assert "too_long" in cmd.result["error"]


def test_inject_instruction_success(svc):
    cmd = svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Keine React-Lösung", "mode": "constraint", "instruction_class": "constraint"},
    )
    assert cmd.status == "applied"
    assert "instruction_id" in cmd.result
    active = svc.get_active_instruction(task_id="t1")
    assert active is not None
    assert active.text == "Keine React-Lösung"


def test_inject_instruction_supersedes_previous(svc):
    cmd1 = svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Alt instruction"},
    )
    svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Neue instruction"},
    )
    old_id = cmd1.result["instruction_id"]
    assert svc._instructions[old_id].status == "superseded"
    active = svc.get_active_instruction(task_id="t1")
    assert active.text == "Neue instruction"


def test_inject_context_note_does_not_supersede(svc):
    cmd1 = svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Main constraint"},
    )
    svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Side note", "mode": "context_note_only"},
    )
    # Main constraint should still be active
    old_id = cmd1.result["instruction_id"]
    assert svc._instructions[old_id].status == "active"


def test_mark_instruction_applied(svc):
    cmd = svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Apply me"},
    )
    instr_id = cmd.result["instruction_id"]
    result = svc.mark_instruction_applied(instr_id)
    assert result is True
    assert svc._instructions[instr_id].status == "applied"
    assert svc.get_active_instruction(task_id="t1") is None


# ── Branch management ─────────────────────────────────────────────────────────

def test_branch_create_and_list(svc):
    b = svc.create_branch(task_id="t1", label="Option A", branch_type="implementation_strategy")
    branches = svc.list_branches(task_id="t1")
    assert len(branches) == 1
    assert branches[0].branch_id == b.branch_id


def test_select_branch_unknown(svc):
    cmd = svc.send_command(command_type="select_branch", task_id="t1", payload={"branch_id": "nonexistent"})
    assert cmd.status == "failed"
    assert "branch_not_found" in cmd.result["error"]


def test_select_branch_success_pauses_others(svc):
    b1 = svc.create_branch(task_id="t1", label="A")
    b2 = svc.create_branch(task_id="t1", label="B")
    b3 = svc.create_branch(task_id="t1", label="C")
    cmd = svc.send_command(command_type="select_branch", task_id="t1", payload={"branch_id": b1.branch_id})
    assert cmd.status == "applied"
    assert svc._branches[b1.branch_id].status == "selected"
    assert svc._branches[b2.branch_id].status == "paused"
    assert svc._branches[b3.branch_id].status == "paused"


def test_select_branch_rejects_same_principal_foreign_task_branch(svc):
    branch = svc.create_branch(task_id="task-b", label="B")
    cmd = svc.send_command(
        command_type="select_branch",
        task_id="task-a",
        payload={"branch_id": branch.branch_id},
    )

    assert cmd.status == "failed"
    assert cmd.result["error"] == "branch_not_found"
    assert branch.status == "proposed"


def test_select_already_selected_branch(svc):
    b = svc.create_branch(task_id="t1", label="A")
    b.status = "selected"
    cmd = svc.send_command(command_type="select_branch", task_id="t1", payload={"branch_id": b.branch_id})
    assert cmd.status == "rejected_by_policy"
    assert "already_selected" in cmd.result["error"]


# ── Approval gates ────────────────────────────────────────────────────────────

def test_approve_gate_no_approval_id(svc):
    cmd = svc.send_command(command_type="approve_gate", task_id="t1", payload={})
    assert cmd.status == "rejected_by_policy"
    assert "approval_id_required" in cmd.result["error"]


def test_approve_gate_success(svc):
    with _mock_approval_decide(result_status="granted"):
        cmd = svc.send_command(
            command_type="approve_gate",
            task_id="t1",
            payload={"approval_id": "appr-123", "reason": "Reviewed"},
        )
    assert cmd.status == "applied"
    assert cmd.result["decision"] == "granted"


def test_deny_gate_success(svc):
    with _mock_approval_decide(result_status="denied"):
        cmd = svc.send_command(
            command_type="deny_gate",
            task_id="t1",
            payload={"approval_id": "appr-123", "reason": "Unsafe"},
        )
    assert cmd.status == "applied"
    assert cmd.result["decision"] == "denied"


def test_approve_gate_expired(svc):
    from agent.services.approval_request_service import ApprovalDecisionError
    with _mock_approval_decide(raises=ApprovalDecisionError("request_expired", 409)):
        cmd = svc.send_command(
            command_type="approve_gate",
            task_id="t1",
            payload={"approval_id": "appr-123"},
        )
    assert cmd.status == "failed"
    assert "request_expired" in cmd.result["error"]


def test_approve_gate_rejects_approval_from_another_task(svc):
    approval_service = MagicMock()
    approval_service.get_request.return_value = MagicMock(
        task_id="task-b",
        goal_id=None,
    )
    with patch(
        "agent.services.approval_request_service.get_approval_request_service",
        return_value=approval_service,
    ):
        cmd = svc.send_command(
            command_type="approve_gate",
            task_id="task-a",
            payload={"approval_id": "foreign-approval"},
        )

    assert cmd.status == "failed"
    assert cmd.result == {"error": "approval_not_found"}
    approval_service.decide_request.assert_not_called()


# ── Control-state read model ──────────────────────────────────────────────────

def test_control_state_no_task(svc):
    with patch("agent.services.approval_request_service.get_approval_request_service") as mock_svc:
        mock_svc.return_value.expire_old_requests = MagicMock()
        mock_svc.return_value.list_requests = MagicMock(return_value=[])
        state = svc.get_control_state(task_id="nonexistent")
    assert state["task_id"] == "nonexistent"
    assert state["run_status"] is None or state["task_status"] is None


def test_control_state_includes_instruction(svc):
    svc.send_command(
        command_type="inject_instruction",
        task_id="t1",
        payload={"text": "Test injection"},
    )
    with patch("agent.services.approval_request_service.get_approval_request_service") as mock_svc:
        mock_svc.return_value.expire_old_requests = MagicMock()
        mock_svc.return_value.list_requests = MagicMock(return_value=[])
        with patch("agent.services.repository_registry.get_repository_registry") as mock_repo:
            task = MagicMock(status="in_progress")
            mock_repo.return_value.task_repo.get_by_id = MagicMock(return_value=task)
            state = svc.get_control_state(task_id="t1")
    assert state["active_instruction"] is not None
    assert state["active_instruction"]["text"] == "Test injection"
    assert state["run_status"] == "applying_intervention"


def test_control_state_waiting_for_approval(svc):
    with patch("agent.services.approval_request_service.get_approval_request_service") as mock_svc:
        mock_svc.return_value.expire_old_requests = MagicMock()
        approval = MagicMock(
            id="appr-1",
            tool_name="file.write",
            risk_class="high",
            k_class=None,
            arguments_digest="abc123",
            target_fingerprint=None,
            scope={},
            expires_at=time.time() + 3600,
            created_at=time.time(),
            content_artifact_ref=None,
        )
        mock_svc.return_value.list_requests = MagicMock(return_value=[approval])
        with patch("agent.services.repository_registry.get_repository_registry") as mock_repo:
            task = MagicMock(status="in_progress")
            mock_repo.return_value.task_repo.get_by_id = MagicMock(return_value=task)
            state = svc.get_control_state(task_id="t1")
    assert state["run_status"] == "waiting_for_approval"
    assert len(state["pending_approvals"]) == 1


def test_control_state_run_status_paused(svc):
    with patch("agent.services.approval_request_service.get_approval_request_service") as mock_svc:
        mock_svc.return_value.expire_old_requests = MagicMock()
        mock_svc.return_value.list_requests = MagicMock(return_value=[])
        with patch("agent.services.repository_registry.get_repository_registry") as mock_repo:
            task = MagicMock(status="paused")
            mock_repo.return_value.task_repo.get_by_id = MagicMock(return_value=task)
            state = svc.get_control_state(task_id="t1")
    assert state["task_status"] == "paused"
    assert state["run_status"] == "paused"


# ── Singleton ─────────────────────────────────────────────────────────────────

def test_get_run_control_service_singleton():
    svc1 = get_run_control_service()
    svc2 = get_run_control_service()
    assert svc1 is svc2


# ── Regression: existing task interventions not broken ─────────────────────────

def test_pause_result_contains_task_id(svc):
    with _mock_intervene(ok=True, data={"id": "t999", "action": "pause", "status": "paused"}):
        cmd = svc.send_command(command_type="pause_run", task_id="t999")
    assert cmd.status == "applied"
    assert cmd.result.get("id") == "t999" or cmd.result.get("status") == "paused"
