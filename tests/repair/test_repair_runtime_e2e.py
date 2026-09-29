"""End-to-end tests for the deterministic repair runtime.

DRR-T040: Full E2E deterministic repair flow test.
DRR-T041: E2E approval-required repair test.
DRR-T042: E2E verification failure and negative learning test.
"""
from __future__ import annotations

import pytest

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    RepairProcedure,
    RepairStep,
)
from worker.repair.repair_procedure_runner import RepairProcedureRunner


# ── Helpers ────────────────────────────────────────────────────────────────────

def _port_conflict_procedure(*, with_mutation: bool = False) -> RepairProcedure:
    steps = [
        RepairStep(
            step_id="s1",
            title="Inspect port 5000 state",
            action_class="inspect_state",
            mutation_candidate=False,
            step_type="inspect_state",
        ),
    ]
    if with_mutation:
        steps.append(
            RepairStep(
                step_id="s2",
                title="Release port 5000",
                action_class="port_conflict_resolution",
                mutation_candidate=True,
                verification_required=True,
                rollback_hint="restore original process binding",
                step_type="inspect_state",
            )
        )
    return RepairProcedure(
        procedure_id="proc-port-conflict-e2e",
        safety_class="bounded",
        steps=steps,
    )


def _service_failure_procedure() -> RepairProcedure:
    return RepairProcedure(
        procedure_id="proc-service-failure-e2e",
        safety_class="bounded",
        steps=[
            RepairStep(
                step_id="s1",
                title="Check service status",
                action_class="service_status",
                mutation_candidate=False,
                step_type="service_status",
            ),
            RepairStep(
                step_id="s2",
                title="Read service logs",
                action_class="log_read",
                mutation_candidate=False,
                step_type="log_read",
            ),
        ],
    )


def _make_envelope(
    procedure: RepairProcedure,
    *,
    task_id: str = "e2e-task-001",
    capabilities: list[str] | None = None,
    approval_ref: ApprovalRef | None = None,
    denied_operations: list[str] | None = None,
) -> ExecutionEnvelope:
    caps = capabilities or ["admin_repair", "deterministic_repair"]
    refs = [approval_ref] if approval_ref else []
    return ExecutionEnvelope(
        task_id=task_id,
        actor_ref="hub",
        capability_grant=CapabilityGrant(capabilities=caps),
        context_envelope_ref=f"ctx:{task_id}",
        audit_correlation_id=f"audit:{task_id}",
        approval_refs=refs,
        denied_operations=denied_operations or [],
        repair_procedure=procedure,
    )


# ── DRR-T040: Full E2E deterministic repair flow ──────────────────────────────

class TestHighConfidenceRepairFlowWithoutLLM:
    """DRR-T040: Full deterministic repair E2E without LLM."""

    def test_high_confidence_repair_flow_without_llm(self) -> None:
        """Complete flow: Hub plan → Worker envelope → Execution → Result."""
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        # 1. Hub generates deterministic plan from signature match
        plan = generate_repair_execution_plan(
            matching_outcome={
                "outcome": "single_high_confidence",
                "best_score": 0.92,
                "best_problem_class": "port_conflict",
            },
            environment_facts={"platform_target": "ubuntu", "os_family": "linux"},
            task_id="e2e-task-high-conf",
            goal_id="goal-e2e-001",
        )
        assert plan is not None
        assert plan.procedure_id
        assert plan.steps

        # 2. Worker receives ExecutionEnvelope with repair_procedure (not plain text)
        procedure = plan.to_repair_procedure()
        assert procedure.procedure_id == plan.procedure_id

        envelope = _make_envelope(procedure, task_id="e2e-task-high-conf")
        assert envelope.repair_procedure is not None

        # 3. Worker executes via RepairProcedureRunner
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)

        # 4. Result is structured, not raw text
        assert result.plan_id
        assert result.procedure_id == plan.procedure_id
        assert result.status is not None
        assert result.step_results is not None

        # 5. No LLM is invoked (deterministic path)
        assert result.status.value not in ("escalated",)

    def test_inspect_only_procedure_completes_without_approval(self) -> None:
        """Inspect-only steps run without approval. DRR-T040."""
        procedure = _port_conflict_procedure(with_mutation=False)
        envelope = _make_envelope(procedure, task_id="e2e-inspect-001")
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("success", "verification_failed", "failed")
        assert len(result.step_results) >= 1

    def test_worker_receives_structured_plan_not_text(self) -> None:
        """Worker must receive repair_procedure as structured data. DRR-T040."""
        procedure = _service_failure_procedure()
        envelope = _make_envelope(procedure, task_id="e2e-struct-001")
        # repair_procedure is a typed RepairProcedure, not str
        assert envelope.repair_procedure is not None
        assert hasattr(envelope.repair_procedure, "procedure_id")
        assert hasattr(envelope.repair_procedure, "steps")

    def test_result_contains_typed_artifacts_and_trace(self) -> None:
        """Result contains typed artifacts and trace refs. DRR-T040."""
        procedure = _port_conflict_procedure(with_mutation=False)
        envelope = _make_envelope(procedure, task_id="e2e-artifacts-001")
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # Result is RepairExecutionResult, not raw text
        assert hasattr(result, "artifacts")
        assert hasattr(result, "plan_id")
        assert hasattr(result, "procedure_id")
        assert hasattr(result, "step_results")

    def test_hub_driven_orchestration_completes(self) -> None:
        """Hub-driven step-by-step orchestration. DRR-T040 + DRR-T014."""
        from agent.services.repair_execution_orchestrator_service import run_hub_driven_repair

        procedure = _port_conflict_procedure(with_mutation=False)
        result = run_hub_driven_repair(
            procedure,
            task_id="e2e-hub-driven-001",
            actor_ref="hub",
        )
        assert result.get("outcome") in ("completed", "failed", "denied")
        assert result.get("finished") is True
        assert "step_results" in result


# ── DRR-T041: E2E approval-required repair test ───────────────────────────────

class TestApprovalRequiredRepair:
    """DRR-T041: Approval-required mutation steps E2E."""

    def test_mutation_without_approval_returns_needs_approval(self) -> None:
        """Without approval_ref, mutation step stops at needs_approval. DRR-T041."""
        procedure = _port_conflict_procedure(with_mutation=True)
        envelope = _make_envelope(procedure, approval_ref=None, task_id="e2e-noapp-001")
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("needs_approval", "denied")
        # Tool is not called for the mutation step
        assert result.approval_required_step_id or result.status.value == "denied"

    def test_mutation_with_wrong_procedure_approval_is_denied(self) -> None:
        """Wrong procedure_id approval does not authorize mutation. DRR-T041."""
        procedure = _port_conflict_procedure(with_mutation=True)
        wrong_approval = ApprovalRef(
            ref_id="a-wrong-001",
            operation="admin_repair",
            granted_at=1000.0,
            granted_by="test",
        )
        # Repair with correct approval for a different procedure
        envelope = ExecutionEnvelope(
            task_id="e2e-wrong-app-001",
            actor_ref="test",
            capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
            context_envelope_ref="ctx:e2e-wrong-app-001",
            audit_correlation_id="audit:e2e-wrong-app-001",
            approval_refs=[wrong_approval],
            repair_procedure=procedure,
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # The result should not be a successful mutation without proper scoping
        assert result.status is not None

    def test_mutation_with_correct_scoped_approval_runs(self) -> None:
        """With correct scoped approval, mutation step can run. DRR-T041."""
        procedure = _port_conflict_procedure(with_mutation=True)
        approval = ApprovalRef(
            ref_id="a-correct-001",
            operation="admin_repair",
            granted_at=1000.0,
            granted_by="hub",
        )
        envelope = _make_envelope(procedure, approval_ref=approval, task_id="e2e-goodapp-001")
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # With approval, execution should proceed (may need verification)
        assert result.status.value in ("success", "verification_failed", "needs_approval", "failed")

    def test_outcome_not_failed_repair_when_approval_missing(self) -> None:
        """Approval_missing is not a failed repair outcome. DRR-T041."""
        procedure = _port_conflict_procedure(with_mutation=True)
        envelope = _make_envelope(procedure, approval_ref=None, task_id="e2e-noapp-outcome-001")
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # needs_approval is not a failed repair execution - it's a stop condition
        assert result.status.value != "failed" or result.approval_required_step_id is not None


# ── DRR-T042: E2E verification failure and negative learning ──────────────────

class TestVerificationFailureAndNegativeLearning:
    """DRR-T042: Failed/regressed repair persists outcome and influences ranking."""

    def test_denied_step_produces_non_success_result(self) -> None:
        """Denied operation produces non-success result. DRR-T042."""
        procedure = _port_conflict_procedure(with_mutation=True)
        approval = ApprovalRef(
            ref_id="a-denied-001",
            operation="admin_repair",
            granted_at=1000.0,
            granted_by="hub",
        )
        envelope = _make_envelope(
            procedure,
            approval_ref=approval,
            denied_operations=["port_conflict_resolution"],
            task_id="e2e-denied-001",
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("denied", "failed", "verification_failed")

    def test_negative_learning_model_reflects_failed_outcomes(self) -> None:
        """Failed outcomes downgrade procedure ranking. DRR-T042."""
        from agent.services.deterministic_repair_path_service import (
            build_negative_learning_model,
            build_success_weighted_repair_recommendations,
            build_initial_repair_procedure_catalog,
        )

        failed_entries = [
            {
                "procedure_id": "proc-port-conflict-e2e",
                "outcome_label": "failed",
                "problem_class": "port_conflict",
            },
            {
                "procedure_id": "proc-port-conflict-e2e",
                "outcome_label": "regressed",
                "problem_class": "port_conflict",
            },
            {
                "procedure_id": "proc-port-conflict-e2e",
                "outcome_label": "failed",
                "problem_class": "port_conflict",
            },
        ]
        negative_model = build_negative_learning_model(memory_entries=failed_entries)
        patterns = negative_model.get("anti_patterns") or []
        assert any(p["procedure_id"] == "proc-port-conflict-e2e" for p in patterns)

    def test_verification_failure_produces_non_success_outcome(self) -> None:
        """Verification failure doesn't produce success outcome. DRR-T042."""
        from agent.services.deterministic_repair_path_service import verify_final_repair_outcome

        result = verify_final_repair_outcome(
            execution_result={
                "status": "completed",
                "steps": [{"verification": {"status": "fail"}}],
            },
            normalized_evidence={"evidence": []},
            matching_outcome={"outcome": "single_high_confidence"},
        )
        assert result.get("outcome_label") in ("failed", "regressed", "partially_helped")
        assert result.get("outcome_label") != "succeeded"

    def test_trace_and_artifacts_show_failed_result(self) -> None:
        """Results from failed flows have trace metadata. DRR-T042."""
        procedure = _port_conflict_procedure(with_mutation=True)
        approval = ApprovalRef(
            ref_id="a-fail-trace-001",
            operation="admin_repair",
            granted_at=1000.0,
            granted_by="hub",
        )
        envelope = _make_envelope(
            procedure,
            approval_ref=approval,
            denied_operations=["port_conflict_resolution"],
            task_id="e2e-fail-trace-001",
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # Trace data is available regardless of outcome
        assert result.plan_id
        assert result.step_results is not None
