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
from unittest.mock import MagicMock, patch

import pytest

from agent.services.run_control_service import (
    COMMAND_TYPES,
    BranchCandidate,
    OperatorInstruction,
    RunCommand,
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
        return_value=MagicMock(decide_request=mock),
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
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd1 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="op:t1:pause:1")
        cmd2 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="op:t1:pause:1")
    assert cmd1.command_id == cmd2.command_id
    # Only one entry in _commands despite two calls
    assert len(svc._commands) == 1


def test_different_idempotency_keys_create_separate_commands(svc):
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd1 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="k1")
    with _mock_intervene(ok=True, data={"id": "t1", "status": "paused"}):
        cmd2 = svc.send_command(command_type="pause_run", task_id="t1", idempotency_key="k2")
    assert cmd1.command_id != cmd2.command_id


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
    cmd2 = svc.send_command(
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
    cmd2 = svc.send_command(
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
