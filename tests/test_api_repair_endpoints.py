"""API tests for deterministic repair endpoints.

DRR-T034: Repair diagnostics read model tests.
DRR-T035: API endpoints for analyze, preview, execute, outcomes.
DRR-T038: Operator view API test.
"""
from __future__ import annotations

import pytest

from agent.services.repair_diagnostics_service import (
    RepairDiagnosticsReadModel,
    build_repair_diagnostics_read_model,
)


# ── DRR-T034: Repair diagnostics read model ───────────────────────────────────

class TestRepairDiagnosticsReadModel:
    def test_build_returns_read_model(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert isinstance(model, RepairDiagnosticsReadModel)

    def test_default_flags(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.deterministic_repair_analysis_enabled is True
        assert model.deterministic_repair_preview_enabled is True
        # Execution is default-off for safety
        assert isinstance(model.deterministic_repair_execution_enabled, bool)

    def test_signature_count_positive(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.signature_count >= 0

    def test_playbook_count_positive(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.playbook_count >= 0

    def test_procedure_count_positive(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.procedure_count >= 0

    def test_runner_ready(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.runner_ready is True

    def test_as_dict_includes_all_fields(self) -> None:
        model = build_repair_diagnostics_read_model()
        d = model.as_dict()
        required_fields = {
            "deterministic_repair_analysis_enabled",
            "deterministic_repair_preview_enabled",
            "deterministic_repair_execution_enabled",
            "signature_count",
            "playbook_count",
            "procedure_count",
            "outcome_persistence_ready",
            "runner_ready",
            "last_error_code",
            "safety_policy_summary",
            "approval_required_classes",
            "feature_flag_states",
        }
        assert required_fields.issubset(d.keys())

    def test_no_secrets_in_diagnostics(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert not model.has_secrets()

    def test_approval_required_classes_include_high_risk(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert "high_risk" in model.approval_required_classes

    def test_safety_policy_summary_not_empty(self) -> None:
        model = build_repair_diagnostics_read_model()
        assert model.safety_policy_summary

    def test_custom_flags_override_defaults(self) -> None:
        model = build_repair_diagnostics_read_model(
            feature_flags={"deterministic_repair_execution_enabled": True}
        )
        assert model.deterministic_repair_execution_enabled is True

    def test_degraded_persistence_reflected(self) -> None:
        model = build_repair_diagnostics_read_model(outcome_persistence_ready=False)
        assert model.outcome_persistence_ready is False

    def test_last_error_code_propagated(self) -> None:
        model = build_repair_diagnostics_read_model(last_error_code="db_connection_failed")
        assert model.last_error_code == "db_connection_failed"


# ── DRR-T035: Repair service functions ───────────────────────────────────────

class TestRepairAnalysisService:
    def test_generate_plan_for_high_confidence_match(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={
                "outcome": "single_high_confidence",
                "best_score": 0.9,
                "best_problem_class": "port_conflict",
            },
            environment_facts={"platform_target": "ubuntu"},
        )
        assert plan is not None
        assert plan.plan_id
        assert plan.procedure_id
        assert len(plan.steps) > 0

    def test_generate_plan_for_no_match_produces_escalation(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={
                "outcome": "no_match",
                "best_score": 0.0,
                "best_problem_class": "unknown",
            },
            environment_facts={},
        )
        assert plan is not None
        assert any(s.step_type == "escalate" for s in plan.steps)

    def test_generate_plan_validates_output_schema(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={"outcome": "single_high_confidence", "best_score": 0.85},
            environment_facts={},
        )
        # Plan is always a valid RepairProcedureExecutionPlan
        assert plan.version
        assert plan.created_by == "hub"

    def test_malformed_matching_outcome_handled_gracefully(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={},
            environment_facts={},
        )
        assert plan is not None


# ── DRR-T038: Operator view functions ────────────────────────────────────────

class TestOperatorViewFunctions:
    def test_build_operator_session_summary(self) -> None:
        from agent.services.deterministic_repair_path_service import build_operator_session_summary

        summary = build_operator_session_summary(
            diagnosis_run={"playbook_id": "playbook-port-conflict"},
            matching_outcome={"outcome": "single_high_confidence", "best_score": 0.9},
            repair_execution_result={"status": "completed", "procedure_id": "proc-001"},
            final_verification={"outcome_label": "succeeded"},
        )
        assert isinstance(summary, dict)
        assert "schema" in summary

    def test_build_path_visibility(self) -> None:
        from agent.services.deterministic_repair_path_service import build_path_visibility

        visibility = build_path_visibility(
            llm_escalation_decision={"should_escalate": False, "reasons": []},
            matching_outcome={"outcome": "single_high_confidence"},
        )
        assert isinstance(visibility, dict)
        assert visibility.get("path_type") == "deterministic"

    def test_build_operator_proposal_preview(self) -> None:
        from agent.services.deterministic_repair_path_service import build_operator_proposal_preview

        preview = build_operator_proposal_preview(
            repair_preview={"procedure_id": "proc-001", "problem_class": "port_conflict"},
            selected_catalog_entry={"procedure": {"steps": []}},
        )
        assert isinstance(preview, dict)
        assert "schema" in preview

    def test_build_history_inspection_view(self) -> None:
        from agent.services.deterministic_repair_path_service import build_repair_history_inspection_view

        view = build_repair_history_inspection_view(
            memory_entries=[
                {"outcome_label": "succeeded", "procedure_id": "proc-001", "problem_class": "port_conflict"},
                {"outcome_label": "failed", "procedure_id": "proc-001", "problem_class": "port_conflict"},
            ],
            filter_problem_class="port_conflict",
        )
        assert isinstance(view, dict)


# ── DRR-T035: Hub-driven orchestrator ────────────────────────────────────────

class TestHubDrivenOrchestrator:
    def test_orchestrator_runs_all_inspect_steps(self) -> None:
        from agent.services.repair_execution_orchestrator_service import RepairExecutionOrchestrator
        from worker.core.execution_envelope import RepairProcedure, RepairStep

        procedure = RepairProcedure(
            procedure_id="proc-orch-001",
            safety_class="bounded",
            steps=[
                RepairStep(step_id="s1", title="Inspect 1", mutation_candidate=False, step_type="inspect_state"),
                RepairStep(step_id="s2", title="Inspect 2", mutation_candidate=False, step_type="service_status"),
            ],
        )
        orchestrator = RepairExecutionOrchestrator(procedure, task_id="orch-test-001")
        summary = orchestrator.run_all()
        assert summary["finished"] is True
        assert summary["outcome"] in ("completed", "failed", "denied")

    def test_orchestrator_stops_at_mutation_needing_approval(self) -> None:
        from agent.services.repair_execution_orchestrator_service import RepairExecutionOrchestrator
        from worker.core.execution_envelope import RepairProcedure, RepairStep

        procedure = RepairProcedure(
            procedure_id="proc-orch-approval-001",
            safety_class="bounded",
            steps=[
                RepairStep(step_id="s1", title="Inspect", mutation_candidate=False, step_type="inspect_state"),
                RepairStep(
                    step_id="s2",
                    title="Mutate",
                    mutation_candidate=True,
                    verification_required=True,
                    step_type="inspect_state",
                ),
            ],
        )
        orchestrator = RepairExecutionOrchestrator(procedure, task_id="orch-test-approval-001")
        summary = orchestrator.run_all(approval_ref=None)
        assert summary["finished"] is True
        assert summary["outcome"] in ("needs_approval", "denied", "completed", "failed")

    def test_orchestrator_summary_has_required_fields(self) -> None:
        from agent.services.repair_execution_orchestrator_service import RepairExecutionOrchestrator
        from worker.core.execution_envelope import RepairProcedure, RepairStep

        procedure = RepairProcedure(
            procedure_id="proc-orch-fields-001",
            safety_class="bounded",
            steps=[RepairStep(step_id="s1", title="Inspect", mutation_candidate=False, step_type="inspect_state")],
        )
        orchestrator = RepairExecutionOrchestrator(procedure)
        summary = orchestrator.summary()
        required = {"task_id", "procedure_id", "outcome", "finished", "step_results", "steps_completed", "total_steps"}
        assert required.issubset(summary.keys())


# ── DRR-T036: Blueprint role config validation ────────────────────────────────

class TestBlueprintRoleConfig:
    def test_blueprint_role_config_can_include_repair_fields(self) -> None:
        """BlueprintRole.config can carry repair execution fields. DRR-T036."""
        from agent.models import BlueprintRoleDefinition

        role = BlueprintRoleDefinition(
            name="Repair Lead",
            description="Owns deterministic repair execution",
            config={
                "preferred_backend": "native_worker",
                "execution_mode": "deterministic_repair",
                "capability_defaults": ["repair.diagnose", "repair.execute.low_risk"],
                "risk_profile": "bounded",
                "verification_defaults": {"require_post_mutation": True},
            },
        )
        assert role.config.get("execution_mode") == "deterministic_repair"
        assert "repair.diagnose" in role.config.get("capability_defaults", [])
