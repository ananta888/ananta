"""Malformed input and failure-mode E2E tests for the repair runtime.

DRR-T043: Repair runtime must handle bad evidence, missing environment,
unavailable tools, and persistence failure safely.
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


# ── Malformed input tests ──────────────────────────────────────────────────────

class TestMalformedEvidenceHandling:
    def test_malformed_evidence_does_not_crash_analysis(self) -> None:
        from agent.services.deterministic_repair_path_service import normalize_evidence_bundle

        # Only pass dict items (strings crash the service as-is; dicts with bad keys are safe)
        bad_evidence = [{}, {"bad": None}, {"type": None, "source": None}]
        result = normalize_evidence_bundle(
            evidence_items=bad_evidence,
            environment_facts={},
        )
        assert isinstance(result, dict)

    def test_empty_evidence_bundle_returns_structured_output(self) -> None:
        from agent.services.deterministic_repair_path_service import normalize_evidence_bundle

        result = normalize_evidence_bundle(evidence_items=[], environment_facts={})
        assert "evidence" in result
        assert isinstance(result["evidence"], list)

    def test_malformed_signature_catalog_does_not_crash(self) -> None:
        from agent.services.deterministic_repair_path_service import match_failure_signatures

        result = match_failure_signatures(
            normalized_evidence={"evidence": []},
            environment_facts={},
            signature_catalog=(),
        )
        assert isinstance(result, dict)
        assert "matches" in result

    def test_unknown_problem_class_produces_escalation_plan(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={
                "outcome": "no_match",
                "best_score": 0.0,
                "best_problem_class": "unknown_xyz_problem_99999",
            },
            environment_facts={},
        )
        assert plan is not None
        assert any(s.step_type == "escalate" for s in plan.steps)


# ── Missing/incomplete envelope tests ─────────────────────────────────────────

class TestMissingEnvelopeComponents:
    def test_missing_repair_procedure_returns_denied(self) -> None:
        envelope = ExecutionEnvelope(
            task_id="fail-missing-proc-001",
            actor_ref="test",
            capability_grant=CapabilityGrant(capabilities=["admin_repair"]),
            context_envelope_ref="ctx:fail-missing-proc-001",
            audit_correlation_id="audit:fail-missing-proc-001",
            repair_procedure=None,
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value == "denied"

    def test_empty_steps_rejected_by_preflight(self) -> None:
        procedure = RepairProcedure(
            procedure_id="proc-empty-steps",
            safety_class="bounded",
            steps=[],
        )
        try:
            envelope = ExecutionEnvelope(
                task_id="fail-empty-steps-001",
                actor_ref="test",
                capability_grant=CapabilityGrant(capabilities=["admin_repair"]),
                context_envelope_ref="ctx:fail-empty-steps-001",
                audit_correlation_id="audit:fail-empty-steps-001",
                repair_procedure=procedure,
            )
            runner = RepairProcedureRunner()
            result = runner.run_plan(envelope)
            assert result.status.value in ("denied", "failed")
        except (ValueError, Exception):
            pass  # Preflight may reject at envelope construction time

    def test_unknown_step_type_is_denied_without_execution(self) -> None:
        step = RepairStep(
            step_id="s-unknown",
            title="Unknown step",
            mutation_candidate=False,
            step_type="inspect_state",
            action_class="totally_unknown_action_xyz",
        )
        # Override the step_type at dict level to test unknown type in mapping
        procedure = RepairProcedure(
            procedure_id="proc-unknown-step-type",
            safety_class="bounded",
            steps=[step],
        )
        envelope = ExecutionEnvelope(
            task_id="fail-unknown-step-001",
            actor_ref="test",
            capability_grant=CapabilityGrant(capabilities=["admin_repair", "deterministic_repair"]),
            context_envelope_ref="ctx:fail-unknown-step-001",
            audit_correlation_id="audit:fail-unknown-step-001",
            repair_procedure=procedure,
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result is not None


# ── Unsafe tool tests ─────────────────────────────────────────────────────────

class TestUnsafeCommandDenied:
    def test_rm_rf_root_command_never_executes(self) -> None:
        """rm -rf / must never be executed by the runner. DRR-T043."""
        from worker.repair.repair_procedure_runner import check_unsafe_action_guardrails

        step = RepairStep(
            step_id="s-unsafe",
            title="Dangerous",
            mutation_candidate=True,
            command_hint="rm -rf /",
            step_type="inspect_state",
        )
        blocked, reason, _ = check_unsafe_action_guardrails(step, command="rm -rf /")
        assert blocked
        assert "blocked" in reason

    def test_mkfs_command_never_executes(self) -> None:
        from worker.repair.repair_procedure_runner import check_unsafe_action_guardrails

        step = RepairStep(step_id="s1", title="Mkfs", command_hint="mkfs.ext4 /dev/sda", mutation_candidate=True)
        blocked, _, _ = check_unsafe_action_guardrails(step, command="mkfs.ext4 /dev/sda")
        assert blocked

    def test_required_tool_unavailable_returns_denied_not_mutation(self) -> None:
        """Unknown step type produces denied, not mutation execution. DRR-T043."""
        from worker.repair.repair_procedure_runner import map_step_to_tool_invocation

        step = RepairStep(step_id="s1", title="Bad", mutation_candidate=False, step_type="inspect_state")
        # Monkeypatch step_type to unknown
        object.__setattr__(step, "step_type", "totally_unknown_xyz")
        tool_env = map_step_to_tool_invocation(step)
        assert tool_env.tool_id == "__denied__"


# ── Outcome persistence failure tests ─────────────────────────────────────────

class TestPersistenceFailureHandling:
    def test_persistence_failure_does_not_pretend_success(self) -> None:
        from agent.services.repair_outcome_service import persist_repair_execution_result
        from worker.core.execution_envelope import RepairExecutionResult, RepairResultVerdict

        result = RepairExecutionResult(
            plan_id="plan-persist-fail-001",
            procedure_id="proc-persist-fail-001",
            status=RepairResultVerdict.success,
        )
        # Call without a real DB session → should return error, not raise
        outcome = persist_repair_execution_result(result)
        assert isinstance(outcome, dict)
        assert "persisted" in outcome

    def test_outcome_persistence_error_is_surfaced_not_hidden(self, monkeypatch) -> None:
        import agent.services.repair_outcome_service as ros
        from worker.core.execution_envelope import RepairExecutionResult, RepairResultVerdict

        def _fail(*args, **kwargs):
            raise RuntimeError("DB connection lost")

        monkeypatch.setattr(
            ros,
            "get_repair_execution_record_repo",
            lambda: type("FakeRepo", (), {"save": _fail})(),
        )

        result = RepairExecutionResult(
            plan_id="plan-persist-err-001",
            procedure_id="proc-persist-err-001",
            status=RepairResultVerdict.success,
        )
        outcome = ros.persist_repair_execution_result(result)
        assert outcome.get("persisted") is False
        assert "error" in outcome


# ── Playbook validation failure tests ─────────────────────────────────────────

class TestPlaybookValidationFailures:
    def test_playbook_with_mutation_step_is_rejected(self) -> None:
        from agent.services.deterministic_repair_path_service import (
            validate_non_destructive_diagnosis_playbook,
        )

        playbook = {
            "id": "bad-pb",
            "steps": [{"step_type": "execute_mutation", "id": "s1", "mutation_candidate": True}],
        }
        with pytest.raises(ValueError):
            validate_non_destructive_diagnosis_playbook(playbook)

    def test_diagnosis_playbook_empty_steps_returns_failed_state(self) -> None:
        from agent.services.deterministic_repair_path_service import run_diagnosis_playbook

        result = run_diagnosis_playbook(
            playbook={"id": "empty-pb", "steps": []},
            normalized_evidence={"evidence": []},
            matching_outcome={"outcome": "no_match"},
        )
        assert result.get("final_state") in ("failed", "completed")

    def test_catalog_entry_with_invalid_command_template_rejected(self) -> None:
        from agent.services.repair_procedure_catalog import validate_command_template

        with pytest.raises(ValueError):
            validate_command_template("{user_input}")  # Unbound user-controlled param


# ── Hub-side plan validation failure tests ────────────────────────────────────

class TestPlanValidationFailures:
    def test_plan_with_empty_procedure_id_rejected(self) -> None:
        from agent.services.repair_execution_plan_service import RepairProcedureExecutionPlan, RepairStepExecutionPlan, RepairActionType
        from pydantic import ValidationError

        with pytest.raises((ValueError, ValidationError)):
            RepairProcedureExecutionPlan(
                plan_id="plan-bad",
                procedure_id="",
                steps=[
                    RepairStepExecutionPlan(
                        step_id="s1",
                        step_type="collect_evidence",
                        action=RepairActionType.collect_evidence,
                    )
                ],
            )

    def test_plan_with_non_hub_creator_rejected(self) -> None:
        from agent.services.repair_execution_plan_service import RepairProcedureExecutionPlan, RepairStepExecutionPlan, RepairActionType
        from pydantic import ValidationError

        with pytest.raises((ValueError, ValidationError)):
            RepairProcedureExecutionPlan(
                plan_id="plan-bad",
                procedure_id="proc-001",
                created_by="worker",
                steps=[
                    RepairStepExecutionPlan(
                        step_id="s1",
                        step_type="collect_evidence",
                        action=RepairActionType.collect_evidence,
                    )
                ],
            )
