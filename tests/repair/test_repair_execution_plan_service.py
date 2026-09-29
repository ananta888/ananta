"""DRR-T006: RepairProcedureExecutionPlan generator tests."""
from __future__ import annotations

from pydantic import ValidationError
import pytest

from agent.services.repair_execution_plan_service import (
    RepairActionType,
    RepairProcedureExecutionPlan,
    RepairStepExecutionPlan,
    generate_repair_execution_plan,
)


def _make_matching_outcome(*, outcome: str = "single_high_confidence", score: float = 0.85) -> dict:
    return {
        "outcome": outcome,
        "decision": "deterministic_repair_candidate",
        "best_problem_class": "service_start_failure",
        "best_score": score,
        "requires_review": False,
        "requires_llm_escalation": False,
    }


def _make_env_facts(**overrides: str) -> dict:
    return {
        "platform_target": overrides.get("platform_target", "ubuntu"),
        "os_family": "linux",
        "package_manager": "apt_dpkg",
        "service_state": "degraded",
    }


def _make_signature_matching() -> dict:
    return {
        "match_count": 1,
        "matches": [
            {
                "signature_id": "sig-service-restart-loop",
                "problem_class": "service_start_failure",
                "score": 0.85,
                "signature_strength": 0.9,
                "matched_patterns": ["failed to start"],
            }
        ],
    }


class TestRepairStepExecutionPlan:
    def test_valid_step(self) -> None:
        step = RepairStepExecutionPlan(
            step_id="s1",
            step_type="collect_evidence",
            action=RepairActionType.collect_evidence,
        )
        assert step.step_id == "s1"

    def test_unknown_step_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown step_type"):
            RepairStepExecutionPlan(
                step_id="s1",
                step_type="invalid_type",
                action=RepairActionType.collect_evidence,
            )

    def test_unknown_safety_class_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown action_safety_class"):
            RepairStepExecutionPlan(
                step_id="s1",
                step_type="collect_evidence",
                action=RepairActionType.collect_evidence,
                action_safety_class="unknown_class",
            )


class TestRepairProcedureExecutionPlan:
    def test_valid_plan(self) -> None:
        plan = RepairProcedureExecutionPlan(
            plan_id="plan-001",
            procedure_id="proc-test-v1",
            steps=[
                RepairStepExecutionPlan(
                    step_id="s1",
                    step_type="collect_evidence",
                    action=RepairActionType.collect_evidence,
                ),
            ],
        )
        assert plan.created_by == "hub"

    def test_empty_procedure_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RepairProcedureExecutionPlan(
                plan_id="plan-001",
                procedure_id="",
                steps=[
                    RepairStepExecutionPlan(
                        step_id="s1",
                        step_type="collect_evidence",
                        action=RepairActionType.collect_evidence,
                    ),
                ],
            )

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError, match="steps must be non-empty"):
            RepairProcedureExecutionPlan(
                plan_id="plan-001",
                procedure_id="proc-test-v1",
                steps=[],
            )

    def test_non_hub_created_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RepairProcedureExecutionPlan(
                plan_id="plan-001",
                procedure_id="proc-test-v1",
                created_by="worker",
                steps=[
                    RepairStepExecutionPlan(
                        step_id="s1",
                        step_type="collect_evidence",
                        action=RepairActionType.collect_evidence,
                    ),
                ],
            )

    def test_mutation_without_verification_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must have verification_after_step"):
            RepairProcedureExecutionPlan(
                plan_id="plan-001",
                procedure_id="proc-test-v1",
                steps=[
                    RepairStepExecutionPlan(
                        step_id="s1",
                        step_type="command_mutation",
                        action=RepairActionType.command_mutation,
                        mutation_candidate=True,
                        verification_after_step=False,
                    ),
                ],
            )

    def test_to_repair_procedure_conversion(self) -> None:
        plan = RepairProcedureExecutionPlan(
            plan_id="plan-001",
            procedure_id="proc-test-v1",
            problem_class="port_conflict",
            steps=[
                RepairStepExecutionPlan(
                    step_id="s1",
                    step_type="collect_evidence",
                    action=RepairActionType.collect_evidence,
                ),
                RepairStepExecutionPlan(
                    step_id="s2",
                    step_type="command_mutation",
                    action=RepairActionType.command_mutation,
                    mutation_candidate=True,
                    verification_after_step=True,
                ),
            ],
        )
        procedure = plan.to_repair_procedure()
        assert procedure.procedure_id == "proc-test-v1"
        assert len(procedure.steps) == 2


class TestGenerateRepairExecutionPlan:
    def test_high_confidence_generates_full_plan(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(),
            environment_facts=_make_env_facts(),
            signature_matching=_make_signature_matching(),
            selected_catalog_entry={
                "procedure": {
                    "id": "proc-service-start-v1",
                    "steps": [
                        {"id": "mutate-01", "title": "Restart service", "mutation_candidate": True},
                    ],
                },
            },
        )
        assert isinstance(plan, RepairProcedureExecutionPlan)
        assert plan.created_by == "hub"
        assert len(plan.steps) > 0
        assert plan.problem_class == "service_start_failure"
        assert plan.signature_id == "sig-service-restart-loop"
        assert any(s.mutation_candidate for s in plan.steps)

    def test_high_confidence_no_llm(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(outcome="single_high_confidence", score=0.85),
            environment_facts=_make_env_facts(),
            signature_matching=_make_signature_matching(),
        )
        step_types = {s.step_type for s in plan.steps}
        assert "escalate" not in step_types

    def test_low_confidence_no_mutation(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(outcome="low_confidence", score=0.4),
            environment_facts=_make_env_facts(),
            signature_matching=_make_signature_matching(),
        )
        assert not any(s.mutation_candidate for s in plan.steps)

    def test_no_match_generates_escalation(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(outcome="no_match", score=0.0),
            environment_facts=_make_env_facts(),
        )
        step_types = {s.step_type for s in plan.steps}
        assert "escalate" in step_types
        assert not any(s.mutation_candidate for s in plan.steps)

    def test_ambiguous_requires_review(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(outcome="ambiguous_high_confidence", score=0.72),
            environment_facts=_make_env_facts(),
        )
        assert not any(s.mutation_candidate for s in plan.steps)
        step_types = {s.step_type for s in plan.steps}
        assert "escalate" in step_types

    def test_plan_includes_env_facts_hash(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(),
            environment_facts=_make_env_facts(),
        )
        assert len(plan.environment_facts_hash) == 16

    def test_plan_includes_verification_plan(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(),
            environment_facts=_make_env_facts(),
            signature_matching=_make_signature_matching(),
            selected_catalog_entry={
                "procedure": {
                    "id": "proc-service-start-v1",
                    "steps": [
                        {"id": "verify-01", "title": "Check service", "step_type": "verification_check", "mutation_candidate": False},
                    ],
                },
            },
        )
        assert len(plan.verification_plan) > 0

    def test_plan_serialization_roundtrip(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(),
            environment_facts=_make_env_facts(),
        )
        raw = plan.model_dump()
        restored = RepairProcedureExecutionPlan(**raw)
        assert restored.plan_id == plan.plan_id
        assert restored.created_by == "hub"

    def test_plan_with_goal_and_task_ids(self) -> None:
        plan = generate_repair_execution_plan(
            matching_outcome=_make_matching_outcome(),
            environment_facts=_make_env_facts(),
            goal_id="goal-001",
            task_id="task-001",
            policy_decision_ref="policy:allow",
            context_bundle_ref="ctx:bundle-001",
        )
        assert plan.goal_id == "goal-001"
        assert plan.task_id == "task-001"
        assert plan.policy_decision_ref == "policy:allow"
        assert plan.context_bundle_ref == "ctx:bundle-001"

    def test_plan_safety_class_high_risk(self) -> None:
        mo = _make_matching_outcome()
        mo["best_problem_class"] = "permission_issue"
        plan = generate_repair_execution_plan(
            matching_outcome=mo,
            environment_facts=_make_env_facts(),
        )
        assert plan.safety_class == "high_risk"
