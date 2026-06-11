"""ALWA-013/014/015: tests for the workspace-audit hook integration.

The hooks live in:
  • worker_workspace_service.refresh_mutation_baseline (ALWA-013)
  • sgpt_workspace_mutation._hub_check (ALWA-014) + final-answer
    blocked path (ALWA-015)
  • mutation_gate_service.evaluate block path (ALWA-015)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.services.worker_workspace_service import get_worker_workspace_service
from agent.services.mutation_gate_service import get_mutation_gate_service
from agent.services.approval_policy_service import ApprovalDecision
from agent.services.execution_risk_policy_service import ExecutionRiskDecision


def _approval(*, classification: str = "allow", reason_code: str = "ok", operation_class: str = "mutation") -> ApprovalDecision:
    return ApprovalDecision(
        classification=classification,
        reason_code=reason_code,
        required_confirmation_level="operator" if classification == "confirm_required" else "none",
        operation_class=operation_class,
        governance_mode="strict",
        enforced=True,
        policy_source="test",
        details={},
    )


def _risk(*, allowed: bool = True, risk_level: str = "low") -> ExecutionRiskDecision:
    return ExecutionRiskDecision(
        allowed=allowed,
        review_required=False,
        risk_level=risk_level,
        reasons=[] if allowed else [f"execution_risk_denied:{risk_level}"],
        blocked_tools=[],
        details={},
    )


def test_refresh_mutation_baseline_emits_workspace_baseline_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def fake_event(action: str, **kwargs: Any) -> None:
        captured.append((action, kwargs))

    monkeypatch.setattr("agent.common.audit.audit_workspace_mutation_event", fake_event)

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x")
    (ws / "b.py").write_text("y")

    ws_svc = get_worker_workspace_service()
    meta = ws_svc.refresh_mutation_baseline(
        workspace_dir=ws, mutation_mode="controlled_workspace"
    )

    # Baseline was created.
    assert meta.get("baseline_dir")
    assert meta.get("file_count") == 2

    # And the audit event fired with the canonical action name.
    actions = [action for action, _ in captured]
    assert "workspace_baseline_created" in actions
    payload = next(kw for action, kw in captured if action == "workspace_baseline_created")
    assert payload["mutation_mode"] == "controlled_workspace"
    assert payload["baseline_id"] == meta["baseline_dir"]
    assert payload["baseline_hash"]  # sha256 hex
    assert len(payload["baseline_hash"]) == 64
    assert payload["workspace_root_hash_or_id"] == str(ws)
    assert payload["materialized_paths_count"] == 2
    # Baseline event has no diff field.
    assert "diff_hash" not in payload
    assert "policy_decision" not in payload


def test_refresh_mutation_baseline_read_only_emits_no_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.common.audit.audit_workspace_mutation_event",
        lambda action, **kw: captured.append((action, kw)),
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x")

    meta = get_worker_workspace_service().refresh_mutation_baseline(
        workspace_dir=ws, mutation_mode="read_only"
    )
    assert meta["skipped"] == "read_only_mode"
    # read_only is explicitly excluded from the mutating-baseline audit.
    assert captured == []


def test_sgpt_workspace_mutation_uses_workspace_event_constants() -> None:
    """ALWA-014/012: the old AUDIT_WORKER_MUTATION_EVALUATED call site
    in sgpt_workspace_mutation must be gone; the helper-based path
    must be present.
    """
    from agent.common import sgpt_workspace_mutation as mod
    import inspect

    src = inspect.getsource(mod)
    # The legacy constant must no longer be imported / referenced.
    assert "AUDIT_WORKER_MUTATION_EVALUATED" not in src
    # The canonical helper must be used.
    assert "AUDIT_WORKSPACE_MUTATION_EVALUATED" in src
    assert "audit_workspace_mutation_event" in src


def test_mutation_gate_block_emits_workspace_mutation_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ALWA-015: every Block-Decision from MutationGateService.evaluate
    emits a workspace_mutation_blocked event with the reason_code as
    blocked_reason and the task/goal/trace ids.
    """
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.common.audit.audit_workspace_mutation_event",
        lambda action, **kw: captured.append((action, kw)),
    )

    svc = get_mutation_gate_service()
    decision = svc.evaluate(
        command="rm -rf /tmp/demo",
        tool_calls=None,
        task={"id": "task-block-1", "goal_id": "goal-block-1"},
        agent_cfg={"mutation_gate": {"global_deny_mutations": True}},
        approval_decision=_approval(classification="allow"),
        risk_decision=_risk(),
        trace_id="trace-block-1",
    )
    assert decision.classification == "blocked"
    actions = [a for a, _ in captured]
    assert "workspace_mutation_blocked" in actions
    payload = next(kw for a, kw in captured if a == "workspace_mutation_blocked")
    assert payload["blocked_reason"] == "mutation_gate_global_deny"
    assert payload["policy_decision"] == "blocked"
    assert payload["task_id"] == "task-block-1"
    assert payload["goal_id"] == "goal-block-1"
    assert payload["trace_id"] == "trace-block-1"
    assert payload["mutation_mode"] in ("file_write", "system_mutation")


def test_mutation_gate_scoped_mismatch_block_emits_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ALWA-015: the scoped-approval mismatch path also fires the
    audit event (was previously a bare return). We force the mismatch
    via target_fingerprint because the validation order is
    task_id → trace_id → actor → class → target → digest.
    """
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.common.audit.audit_workspace_mutation_event",
        lambda action, **kw: captured.append((action, kw)),
    )

    svc = get_mutation_gate_service()
    task = {
        "id": "task-block-2",
        "goal_id": "goal-block-2",
        "mutation_approval": {
            "task_id": "task-block-2",
            "trace_id": "trace-block-2",
            "actor": "operator",
            "mutation_classes": ["file_write"],
            "expires_at": __import__("time").time() + 600,
            "target_fingerprint": "deadbeef",  # intentionally wrong
        },
    }
    decision = svc.evaluate(
        command="chmod +x scripts/run.sh",
        tool_calls=None,
        task=task,
        agent_cfg={},
        approval_decision=_approval(classification="confirm_required", reason_code="approval_confirmation_required:mutation"),
        risk_decision=_risk(),
        trace_id="trace-block-2",
        actor="operator",
    )
    assert decision.classification == "blocked"
    actions = [a for a, _ in captured]
    assert "workspace_mutation_blocked" in actions
    payload = next(kw for a, kw in captured if a == "workspace_mutation_blocked")
    assert payload["blocked_reason"] == "mutation_scope_mismatch:target"
