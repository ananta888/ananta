"""WFG-011, WFG-012, WFG-013, WFG-014: gate engine, decision model,
queue reconciliation, and failure policy resolution."""

from __future__ import annotations

import time

import pytest

from agent.services.workflow_gate_engine import (
    GateDecision,
    GateDecisionStatus,
    apply_gate_decision_to_task_status,
    evaluate_gate,
    evaluate_gate_checks,
    reconcile_gate_state,
    register_gate_check,
    resolve_gate_failure_policy,
)
from agent.services.workflow_gate_engine import (
    GateCheckResult as _GateCheckResult,  # re-export for type-hint checks
)
from agent.services.workflow_settings import (
    GateFailurePolicy,
    WorkflowMode,
    WorkflowSettings,
)


# ---------------------------------------------------------------------------
# WFG-014: failure policy resolution
# ---------------------------------------------------------------------------


def _settings(
    *,
    mode: WorkflowMode = WorkflowMode.AUTO,
    default_gate_policy: GateFailurePolicy = GateFailurePolicy.BLOCK,
    gate_timeout_seconds: int = 86400,
    audit_enabled: bool = True,
    artifact_flow_enforced: bool = True,
) -> WorkflowSettings:
    return WorkflowSettings(
        mode=mode,
        default_gate_policy=default_gate_policy,
        gate_timeout_seconds=gate_timeout_seconds,
        audit_enabled=audit_enabled,
        artifact_flow_enforced=artifact_flow_enforced,
    )


def test_resolve_policy_prefers_step_override() -> None:
    policy, source = resolve_gate_failure_policy(
        step={"failure_policy": "manual"},
        blueprint_default="skip",
        settings=_settings(default_gate_policy=GateFailurePolicy.BLOCK),
    )
    assert policy == GateFailurePolicy.MANUAL
    assert source == "step"


def test_resolve_policy_falls_back_to_blueprint_default() -> None:
    policy, source = resolve_gate_failure_policy(
        step={},
        blueprint_default="skip",
        settings=_settings(default_gate_policy=GateFailurePolicy.BLOCK),
    )
    assert policy == GateFailurePolicy.SKIP
    assert source == "blueprint_default"


def test_resolve_policy_falls_back_to_deployment_default() -> None:
    policy, source = resolve_gate_failure_policy(
        step=None,
        blueprint_default=None,
        settings=_settings(default_gate_policy=GateFailurePolicy.MANUAL),
    )
    assert policy == GateFailurePolicy.MANUAL
    assert source == "deployment_default"


def test_resolve_policy_invalid_step_value_falls_through() -> None:
    """An unknown step.failure_policy must not crash the queue;
    resolution falls through to the blueprint or deployment default."""
    policy, source = resolve_gate_failure_policy(
        step={"failure_policy": "bogus_value"},
        blueprint_default="skip",
        settings=_settings(),
    )
    assert policy == GateFailurePolicy.SKIP
    assert source == "blueprint_default"


# ---------------------------------------------------------------------------
# WFG-011: gate check engine
# ---------------------------------------------------------------------------


def _plan_tasks_context() -> dict:
    return {
        "plan_tasks": {
            "tasks": [
                {"id": "T1", "title": "X", "acceptance_criteria": ["a"], "depends_on": []},
                {"id": "T2", "title": "Y", "acceptance_criteria": ["b"], "depends_on": ["T1"]},
            ]
        }
    }


def test_evaluate_gate_checks_dict_format() -> None:
    results = evaluate_gate_checks(
        checks={
            "plan_has_small_tasks": {},
            "dependencies_are_acyclic": {},
        },
        task={},
        step={},
        context=_plan_tasks_context(),
    )
    names = [r.name for r in results]
    assert names == ["plan_has_small_tasks", "dependencies_are_acyclic"]
    assert all(r.passed for r in results)


def test_evaluate_gate_checks_list_of_strings() -> None:
    results = evaluate_gate_checks(
        checks=["plan_has_small_tasks"],
        task={},
        step={},
        context=_plan_tasks_context(),
    )
    assert [r.name for r in results] == ["plan_has_small_tasks"]


def test_evaluate_gate_checks_list_of_dicts() -> None:
    results = evaluate_gate_checks(
        checks=[{"name": "plan_has_small_tasks", "params": {}}],
        task={},
        step={},
        context=_plan_tasks_context(),
    )
    assert [r.name for r in results] == ["plan_has_small_tasks"]


def test_evaluate_gate_checks_unknown_check_fails_cleanly() -> None:
    """Unknown check names must produce a failed result with a clear
    reason, not raise — the gate stays deterministic and observable."""
    results = evaluate_gate_checks(
        checks=["no_such_check"],
        task={},
        step={},
        context={},
    )
    assert len(results) == 1
    assert results[0].name == "no_such_check"
    assert results[0].passed is False
    assert "unknown_check" in results[0].reason


def test_evaluate_gate_checks_check_exception_is_caught() -> None:
    def _explode(task, step, ctx):
        raise RuntimeError("boom")

    register_gate_check("exploding_check", _explode)
    try:
        results = evaluate_gate_checks(
            checks=["exploding_check"],
            task={},
            step={},
            context={},
        )
    finally:
        # Unregister to keep test isolation (mutating module-level
        # state).
        from agent.services.workflow_gate_engine import _BUILTIN_GATE_CHECKS
        _BUILTIN_GATE_CHECKS.pop("exploding_check", None)
    assert results[0].passed is False
    assert "check_raised" in results[0].reason


def test_evaluate_gate_checks_plan_has_small_tasks_fails_on_empty() -> None:
    results = evaluate_gate_checks(
        checks=["plan_has_small_tasks"],
        task={},
        step={},
        context={"plan_tasks": {"tasks": []}},
    )
    assert results[0].passed is False
    assert "fewer than 2" in results[0].reason


def test_evaluate_gate_checks_dependencies_acyclic_detects_cycle() -> None:
    context = {
        "plan_tasks": {
            "tasks": [
                {"id": "A", "depends_on": ["B"]},
                {"id": "B", "depends_on": ["A"]},
            ]
        }
    }
    results = evaluate_gate_checks(
        checks=["dependencies_are_acyclic"],
        task={},
        step={},
        context=context,
    )
    assert results[0].passed is False
    assert "cycle" in results[0].reason


def test_evaluate_gate_checks_security_review_needed() -> None:
    # No tools / credentials → pass without security_reviewer.
    results = evaluate_gate_checks(
        checks=["security_review_needed_if_tools_or_credentials_are_used"],
        task={},
        step={},
        context={},
    )
    assert results[0].passed is True

    # Tools used but no security_reviewer downstream → fail.
    results = evaluate_gate_checks(
        checks=["security_review_needed_if_tools_or_credentials_are_used"],
        task={},
        step={},
        context={"tool_usage": ["shell"], "security_reviewer_present": False},
    )
    assert results[0].passed is False

    # Tools used and security_reviewer downstream → pass.
    results = evaluate_gate_checks(
        checks=["security_review_needed_if_tools_or_credentials_are_used"],
        task={},
        step={},
        context={"tool_usage": ["shell"], "security_reviewer_present": True},
    )
    assert results[0].passed is True


# ---------------------------------------------------------------------------
# WFG-012: gate decision model
# ---------------------------------------------------------------------------


def test_evaluate_gate_passes_when_all_checks_pass() -> None:
    decision = evaluate_gate(
        task={},
        step={"failure_policy": "block"},
        checks={"plan_has_small_tasks": {}, "dependencies_are_acyclic": {}},
        context=_plan_tasks_context(),
    )
    assert decision.status == GateDecisionStatus.PASSED
    assert decision.failure_policy == GateFailurePolicy.BLOCK
    assert decision.policy_source == "step"
    assert decision.all_checks_passed
    assert decision.to_dict()["schema"] == "workflow_gate_decision.v1"


def test_evaluate_gate_failed_with_block_policy_yields_failed() -> None:
    decision = evaluate_gate(
        task={},
        step={"failure_policy": "block"},
        checks=["plan_has_small_tasks"],
        context={"plan_tasks": {"tasks": []}},
    )
    assert decision.status == GateDecisionStatus.FAILED
    assert "plan_has_small_tasks" in decision.reason_details["failed_checks"]


def test_evaluate_gate_failed_with_skip_policy_yields_skipped() -> None:
    decision = evaluate_gate(
        task={},
        step={"failure_policy": "skip"},
        checks=["plan_has_small_tasks"],
        context={"plan_tasks": {"tasks": []}},
    )
    assert decision.status == GateDecisionStatus.SKIPPED


def test_evaluate_gate_failed_with_manual_policy_yields_pending() -> None:
    decision = evaluate_gate(
        task={},
        step={"failure_policy": "manual"},
        checks=["plan_has_small_tasks"],
        context={"plan_tasks": {"tasks": []}},
    )
    assert decision.status == GateDecisionStatus.PENDING
    assert decision.reason_code == "gate_failed_pending_human_approval"


def test_evaluate_gate_no_checks_yields_passed() -> None:
    decision = evaluate_gate(
        task={},
        step={},
        checks=None,
        context={},
    )
    assert decision.status == GateDecisionStatus.PASSED
    assert decision.reason_code == "no_checks_configured"


# ---------------------------------------------------------------------------
# WFG-012: apply gate decision to task status
# ---------------------------------------------------------------------------


def test_apply_gate_decision_passes_maps_to_completed() -> None:
    decision = GateDecision(
        status=GateDecisionStatus.PASSED,
        check_results=(),
        failure_policy=GateFailurePolicy.BLOCK,
        policy_source="step",
    )
    new_status, reason, gate_block = apply_gate_decision_to_task_status(
        current_status="blocked", decision=decision
    )
    assert new_status == "completed"
    assert reason == "gate_passed"
    assert gate_block == "passed"


def test_apply_gate_decision_failed_maps_to_failed_status() -> None:
    decision = GateDecision(
        status=GateDecisionStatus.FAILED,
        check_results=(),
        failure_policy=GateFailurePolicy.BLOCK,
        policy_source="step",
    )
    new_status, reason, gate_block = apply_gate_decision_to_task_status(
        current_status="blocked", decision=decision
    )
    assert new_status == "failed"
    assert reason == "gate_failed"
    assert gate_block == "failed"


def test_apply_gate_decision_skipped_unblocks_downstream() -> None:
    """A SKIPPED gate (failure_policy=skip) must unblock downstream
    steps just like a passed gate, otherwise the workflow would
    deadlock when a non-critical check fails."""
    decision = GateDecision(
        status=GateDecisionStatus.SKIPPED,
        check_results=(),
        failure_policy=GateFailurePolicy.SKIP,
        policy_source="step",
    )
    new_status, reason, gate_block = apply_gate_decision_to_task_status(
        current_status="blocked", decision=decision
    )
    assert new_status == "completed"
    assert reason == "gate_skipped_by_policy"
    assert gate_block == "skipped"


# ---------------------------------------------------------------------------
# WFG-013: queue reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_gate_state_returns_noop_for_non_gate_task() -> None:
    action = reconcile_gate_state(
        task={"status": "todo"},
        step={"gate": False},
        settings=_settings(),
    )
    assert action["action"] == "noop"


def test_reconcile_gate_state_unblocks_when_passed() -> None:
    task = {
        "status": "blocked",
        "verification_status": {"gate": "passed"},
    }
    action = reconcile_gate_state(task=task, step={"gate": True}, settings=_settings())
    assert action["action"] == "unblock_downstream"
    assert action["decision_status"] == "passed"


def test_reconcile_gate_state_unblocks_when_skipped() -> None:
    """A SKIPPED gate (failure_policy=skip) must also unblock
    downstream steps; the queue must not treat skip as a hard stop."""
    task = {
        "status": "blocked",
        "verification_status": {"gate": "skipped"},
    }
    action = reconcile_gate_state(task=task, step={"gate": True}, settings=_settings())
    assert action["action"] == "unblock_downstream"
    assert action["decision_status"] == "skipped"


def test_reconcile_gate_state_keeps_blocked_on_failed() -> None:
    task = {
        "status": "blocked",
        "verification_status": {"gate": "failed"},
    }
    action = reconcile_gate_state(task=task, step={"gate": True}, settings=_settings())
    assert action["action"] == "keep_blocked"
    assert action["reason_code"] == "gate_failed"


def test_reconcile_gate_state_keeps_blocked_on_pending_approval() -> None:
    task = {
        "status": "blocked",
        "verification_status": {"gate": "pending_approval"},
    }
    action = reconcile_gate_state(task=task, step={"gate": True}, settings=_settings())
    assert action["action"] == "keep_blocked"
    assert action["reason_code"] == "gate_failed_pending_human_approval"


def test_reconcile_gate_state_marks_stale_on_timeout() -> None:
    now = time.time()
    task = {
        "status": "blocked",
        "verification_status": {},
        "status_reason_details": {"gate_pending_since": now - 100000},
    }
    settings = _settings(gate_timeout_seconds=60)
    action = reconcile_gate_state(
        task=task, step={"gate": True}, settings=settings, now_ts=now
    )
    assert action["action"] == "mark_stale"
    assert action["reason_code"] == "gate_timeout"


def test_reconcile_gate_state_keeps_pending_when_within_timeout() -> None:
    now = time.time()
    task = {
        "status": "blocked",
        "verification_status": {},
        "status_reason_details": {"gate_pending_since": now - 5},
    }
    settings = _settings(gate_timeout_seconds=60)
    action = reconcile_gate_state(
        task=task, step={"gate": True}, settings=settings, now_ts=now
    )
    assert action["action"] == "keep_blocked"
    assert action["reason_code"] == "gate_pending"
