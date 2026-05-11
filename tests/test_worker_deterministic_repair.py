from __future__ import annotations

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    RepairProcedure,
    RepairStep,
)
from worker.core.preflight import PreflightGate


def _make_repair_envelope(*, with_procedure: bool = True) -> ExecutionEnvelope:
    kwargs = {
        "task_id": "worker-repair-001",
        "actor_ref": "test",
        "capability_grant": CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
        "context_envelope_ref": "ctx:repair-001",
        "audit_correlation_id": "audit:repair-001",
        "approval_refs": [
            ApprovalRef(
                ref_id="approval-repair-001",
                operation="admin_repair",
                granted_at=1000.0,
                granted_by="test",
            ),
        ],
    }
    if with_procedure:
        kwargs["repair_procedure"] = RepairProcedure(
            procedure_id="repair-proc-001",
            safety_class="bounded",
            diagnosis={"problem_class": "port_conflict"},
            steps=[
                RepairStep(
                    step_id="step-01",
                    title="Inspect port 5000",
                    action_class="inspect_state",
                    mutation_candidate=False,
                ),
                RepairStep(
                    step_id="step-02",
                    title="Release port 5000",
                    action_class="port_conflict_resolution",
                    mutation_candidate=True,
                    verification_required=True,
                    expected_verification="port_5000_free",
                ),
                RepairStep(
                    step_id="step-03",
                    title="Verify service starts",
                    action_class="verification_check",
                    mutation_candidate=False,
                    verification_required=True,
                    expected_verification="service_healthy",
                ),
            ],
        )
    return ExecutionEnvelope(**kwargs)


def test_preflight_allows_repair_envelope_with_procedure() -> None:
    envelope = _make_repair_envelope(with_procedure=True)
    gate = PreflightGate()
    result = gate.check(envelope)
    assert result.allowed, f"Repair envelope should pass preflight: {result.reason_code}"


def test_preflight_blocks_repair_envelope_without_procedure() -> None:
    envelope = _make_repair_envelope(with_procedure=False)
    gate = PreflightGate()
    result = gate.check(envelope)
    assert not result.allowed, "Repair envelope without procedure should be blocked"
    assert result.reason_code == "missing_capability"


def test_preflight_blocks_empty_procedure_steps() -> None:
    envelope = _make_repair_envelope(with_procedure=True)
    envelope.repair_procedure.steps = []
    gate = PreflightGate()
    result = gate.check(envelope)
    assert not result.allowed, "Empty procedure steps should be blocked"


def test_controlled_worker_loop_repair_includes_structured_result() -> None:
    """The loop state dict includes the structured RepairExecutionResult for persistence."""
    from worker.loop.controlled_worker_loop import run_controlled_worker_loop

    procedure = RepairProcedure(
        procedure_id="repair-proc-result-001",
        safety_class="bounded",
        steps=[
            RepairStep(step_id="s1", title="Inspect", mutation_candidate=False),
        ],
    )
    result = run_controlled_worker_loop(
        task_id="loop-repair-result-001",
        trace_id="tr-repair-result-001",
        context_hash="ctx-repair-result-001",
        policy_decision="allow",
        approval_ref=None,
        iteration_outcomes=[],
        repair_procedure=procedure,
    )
    assert "repair_execution_result" in result
    assert result["repair_execution_result"]["procedure_id"] == "repair-proc-result-001"
    assert result["repair_execution_result"]["status"] == "success"


def test_controlled_worker_loop_deterministic_repair_path() -> None:
    from worker.loop.controlled_worker_loop import run_controlled_worker_loop

    procedure = RepairProcedure(
        procedure_id="repair-proc-loop-001",
        safety_class="bounded",
        steps=[
            RepairStep(step_id="s1", title="Inspect", mutation_candidate=False),
            RepairStep(
                step_id="s2",
                title="Fix",
                mutation_candidate=True,
                verification_required=True,
            ),
        ],
    )
    result = run_controlled_worker_loop(
        task_id="loop-repair-001",
        trace_id="tr-repair-001",
        context_hash="ctx-repair-001",
        policy_decision="allow",
        approval_ref={
            "approval_id": "a-001",
            "granted_at": 1000.0,
            "granted_by": "test",
            "operation": "admin_repair",
        },
        iteration_outcomes=[],
        repair_procedure=procedure,
        capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
    )
    assert result["status"] == "completed"
    assert result["stop_reason"] == "deterministic_repair_completed"
    assert result["execution_profile"] == "deterministic_repair"
    assert "repair_procedure" in result
    assert len(result["artifacts"]) == 2


def test_controlled_worker_loop_deterministic_repair_needs_approval() -> None:
    from worker.loop.controlled_worker_loop import run_controlled_worker_loop

    procedure = RepairProcedure(
        procedure_id="repair-proc-loop-002",
        safety_class="bounded",
        steps=[
            RepairStep(step_id="s1", title="Inspect", mutation_candidate=False),
            RepairStep(
                step_id="s2",
                title="Mutate",
                mutation_candidate=True,
                verification_required=True,
            ),
        ],
    )
    result = run_controlled_worker_loop(
        task_id="loop-repair-002",
        trace_id="tr-repair-002",
        context_hash="ctx-repair-002",
        policy_decision="allow",
        approval_ref=None,
        iteration_outcomes=[],
        repair_procedure=procedure,
        capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
    )
    assert result["status"] == "needs_approval"
    assert "approval_required" in result["stop_reason"]


def test_controlled_worker_loop_repair_denied_by_policy() -> None:
    from worker.loop.controlled_worker_loop import run_controlled_worker_loop

    procedure = RepairProcedure(
        procedure_id="repair-proc-loop-003",
        safety_class="bounded",
        steps=[
            RepairStep(step_id="s1", title="Inspect", mutation_candidate=False),
        ],
    )
    result = run_controlled_worker_loop(
        task_id="loop-repair-003",
        trace_id="tr-repair-003",
        context_hash="ctx-repair-003",
        policy_decision="deny",
        approval_ref=None,
        iteration_outcomes=[],
        repair_procedure=procedure,
        capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
    )
    assert result["status"] == "stopped"
    assert result["stop_reason"] == "policy_denied"


def test_controlled_worker_loop_repair_verification_failure() -> None:
    """Verification failure is surfaced correctly."""
    from worker.core.execution_envelope import ExecutionEnvelope
    from worker.repair.repair_procedure_runner import RepairProcedureRunner

    envelope = ExecutionEnvelope(
        task_id="loop-repair-verify-fail-001",
        actor_ref="test",
        capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
        context_envelope_ref="ctx-verify-fail-001",
        audit_correlation_id="audit-verify-fail-001",
        denied_operations=["port_conflict_resolution"],
        repair_procedure=RepairProcedure(
            procedure_id="repair-proc-verify-fail-001",
            safety_class="bounded",
            steps=[
                RepairStep(
                    step_id="s1",
                    title="Fix port",
                    mutation_candidate=True,
                    verification_required=True,
                    action_class="port_conflict_resolution",
                ),
            ],
        ),
    )
    runner = RepairProcedureRunner()
    result = runner.run_plan(envelope)
    assert result.status.value == "denied"
    assert "operation_denied" in result.step_results[0].reason_code


def test_controlled_worker_loop_repair_rollback_hint() -> None:
    """Rollback hint is propagated through step results."""
    from worker.core.execution_envelope import ExecutionEnvelope
    from worker.repair.repair_procedure_runner import RepairProcedureRunner

    envelope = ExecutionEnvelope(
        task_id="loop-repair-rollback-001",
        actor_ref="test",
        capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
        context_envelope_ref="ctx-rollback-001",
        audit_correlation_id="audit-rollback-001",
        approval_refs=[
            ApprovalRef(
                ref_id="a-rollback",
                operation="admin_repair",
                granted_at=1000.0,
                granted_by="test",
            ),
        ],
        repair_procedure=RepairProcedure(
            procedure_id="repair-proc-rollback-001",
            safety_class="bounded",
            steps=[
                RepairStep(
                    step_id="s1",
                    title="Fix with rollback",
                    mutation_candidate=True,
                    verification_required=True,
                    rollback_hint="restart original process",
                ),
            ],
        ),
    )
    runner = RepairProcedureRunner()
    result = runner.run_plan(envelope)
    assert result.status.value == "success"
    assert len(result.step_results) == 1
    assert result.step_results[0].rollback_hint_used == "restart original process"
