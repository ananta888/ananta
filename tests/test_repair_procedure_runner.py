"""DRR-T011: Worker RepairProcedureRunner tests."""
from __future__ import annotations

from pydantic import ValidationError
import pytest

from worker.core.execution_envelope import (
    ApprovalRef,
    CapabilityGrant,
    ExecutionEnvelope,
    RepairExecutionResult,
    RepairProcedure,
    RepairResultVerdict,
    RepairStep,
    RepairStepResult,
    RepairStepResultStatus,
)
from worker.repair.repair_procedure_runner import (
    RepairProcedureRunner,
    RepairRunnerState,
    integrate_runner_with_envelope,
)


def _make_envelope(*, with_procedure: bool = True, has_approval: bool = True) -> ExecutionEnvelope:
    approval_refs = []
    if has_approval:
        approval_refs.append(
            ApprovalRef(
                ref_id="approval-001",
                operation="admin_repair",
                granted_at=1000.0,
                granted_by="test",
            )
        )
    kwargs = {
        "task_id": "runner-test-001",
        "actor_ref": "test",
        "capability_grant": CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
        "context_envelope_ref": "ctx:runner-001",
        "audit_correlation_id": "audit:runner-001",
        "approval_refs": approval_refs,
    }
    if with_procedure:
        kwargs["repair_procedure"] = RepairProcedure(
            procedure_id="proc-runner-test-v1",
            safety_class="bounded",
            diagnosis={"problem_class": "port_conflict"},
            steps=[
                RepairStep(
                    step_id="s1",
                    title="Inspect port",
                    action_class="inspect_state",
                    mutation_candidate=False,
                ),
                RepairStep(
                    step_id="s2",
                    title="Fix port conflict",
                    action_class="port_conflict_resolution",
                    mutation_candidate=True,
                    verification_required=True,
                    command_hint="release port 5000",
                    rollback_hint="restart original process",
                ),
            ],
        )
    return ExecutionEnvelope(**kwargs)


class TestRepairRunnerState:
    def test_valid_transitions(self) -> None:
        assert RepairRunnerState.detected.next(RepairRunnerState.diagnosing)
        assert RepairRunnerState.diagnosing.next(RepairRunnerState.proposing)
        assert RepairRunnerState.proposing.next(RepairRunnerState.executing)
        assert RepairRunnerState.executing.next(RepairRunnerState.verifying)
        assert RepairRunnerState.verifying.next(RepairRunnerState.succeeded)

    def test_invalid_transitions(self) -> None:
        assert not RepairRunnerState.detected.next(RepairRunnerState.succeeded)
        assert not RepairRunnerState.executing.next(RepairRunnerState.proposing)

    def test_terminal_states(self) -> None:
        assert RepairRunnerState.succeeded.terminal
        assert RepairRunnerState.failed.terminal
        assert RepairRunnerState.escalated.terminal
        assert not RepairRunnerState.executing.terminal


class TestRepairProcedureRunner:
    def test_runner_import_and_create(self) -> None:
        runner = RepairProcedureRunner()
        assert runner is not None

    def test_run_plan_success(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert isinstance(result, RepairExecutionResult)
        assert result.status == RepairResultVerdict.success
        assert len(result.completed_steps) == 2

    def test_run_plan_without_procedure_returns_denied(self) -> None:
        envelope = _make_envelope(with_procedure=False)
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.denied

    def test_dry_run_returns_preview(self) -> None:
        envelope = _make_envelope(with_procedure=True)
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope, dry_run=True)
        assert result.status == RepairResultVerdict.success
        assert result.outcome_label == "preview"

    def test_step_result_contains_side_effects_for_mutation(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert len(result.step_results) == 2
        mutation_result = result.step_results[1]
        assert mutation_result.step_id == "s2"
        assert mutation_result.status == RepairStepResultStatus.success
        assert mutation_result.verification_result is not None

    def test_integrate_runner_with_envelope(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        worker_result = integrate_runner_with_envelope(envelope)
        assert worker_result.task_id == "runner-test-001"
        assert "success" in worker_result.status.value

    def test_integrate_without_procedure_denied(self) -> None:
        envelope = _make_envelope(with_procedure=False)
        worker_result = integrate_runner_with_envelope(envelope)
        assert "denied" in worker_result.status.value

    def test_run_step_single_step(self) -> None:
        runner = RepairProcedureRunner()
        step_result = runner.run_step({
            "step": {
                "step_id": "single-step",
                "title": "Single step",
                "action_class": "inspect_state",
                "mutation_candidate": False,
            },
            "execution_envelope": _make_envelope(with_procedure=True, has_approval=True).model_dump(),
        })
        assert step_result.status == RepairStepResultStatus.success
        assert step_result.step_id == "single-step"


class TestRepairRunnerEdgeCases:
    def test_missing_approval_for_mutation(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=False)
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.needs_approval

    def test_empty_procedure_steps_blocked_by_preflight(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        envelope.repair_procedure.steps = []
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.denied


class TestSafetyClassEnforcement:
    def test_inspect_only_passes_without_approval(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=False)
        envelope.repair_procedure.steps = [
            RepairStep(
                step_id="inspect",
                title="Inspect only",
                action_class="inspect_state",
                mutation_candidate=False,
            ),
        ]
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.success

    def test_confirm_required_blocked_without_approval(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=False)
        envelope.repair_procedure = RepairProcedure(
            procedure_id="proc-confirm-test",
            safety_class="confirm_required",
            steps=[
                RepairStep(
                    step_id="mutate",
                    title="Mutation step",
                    action_class="port_conflict_resolution",
                    mutation_candidate=True,
                    verification_required=True,
                ),
            ],
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.needs_approval

    def test_high_risk_blocked_without_capability(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        envelope.repair_procedure = RepairProcedure(
            procedure_id="proc-high-risk",
            safety_class="high_risk",
            steps=[
                RepairStep(
                    step_id="risky",
                    title="High risk step",
                    action_class="permission_fix",
                    mutation_candidate=True,
                    verification_required=True,
                ),
            ],
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.denied

    def test_high_risk_passes_with_proper_capability(self) -> None:
        envelope = _make_envelope(with_procedure=True, has_approval=True)
        envelope.capability_grant.capabilities.append("repair.execute.approval_gated")
        envelope.repair_procedure = RepairProcedure(
            procedure_id="proc-high-risk-allowed",
            safety_class="high_risk",
            steps=[
                RepairStep(
                    step_id="risky",
                    title="High risk step",
                    action_class="permission_fix",
                    mutation_candidate=True,
                    verification_required=True,
                ),
            ],
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status == RepairResultVerdict.success
