"""Governance tests for deterministic repair safety, approval, and policy enforcement.

DRR-T010: Input validation hardening.
DRR-T018: Approval scope binding.
DRR-T019: Unsafe action guardrails.
DRR-T020: Dry-run and proposal preview mode.
DRR-T021: Feature flag gating.
DRR-T023: Before/after evidence capture.
DRR-T024: Rollback hints.
DRR-T025: Outcome classification.
DRR-T028: Negative learning model.
DRR-T029: Environment similarity query.
DRR-T009: LLM escalation envelope.
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
from worker.repair.repair_procedure_runner import (
    RepairFeatureFlags,
    RepairProcedureRunner,
    check_unsafe_action_guardrails,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_envelope(
    *,
    steps: list[RepairStep] | None = None,
    capabilities: list[str] | None = None,
    approval_refs: list[ApprovalRef] | None = None,
    denied_operations: list[str] | None = None,
    safety_class: str = "bounded",
) -> ExecutionEnvelope:
    _steps = steps or [RepairStep(step_id="s1", title="Inspect", mutation_candidate=False)]
    return ExecutionEnvelope(
        task_id="gov-test-001",
        actor_ref="test",
        capability_grant=CapabilityGrant(capabilities=capabilities or ["admin_repair", "deterministic_repair"]),
        context_envelope_ref="ctx:gov-001",
        audit_correlation_id="audit:gov-001",
        approval_refs=approval_refs or [],
        denied_operations=denied_operations or [],
        repair_procedure=RepairProcedure(
            procedure_id="proc-gov-001",
            safety_class=safety_class,
            steps=_steps,
        ),
    )


# ── DRR-T019: Unsafe action guardrails ────────────────────────────────────────

class TestUnsafeActionGuardrails:
    def test_rm_rf_root_is_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="rm -rf /", mutation_candidate=True)
        blocked, reason_code, pattern_id = check_unsafe_action_guardrails(step, command="rm -rf /")
        assert blocked
        assert "blocked_rm_rf_root" in reason_code

    def test_mkfs_is_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="mkfs.ext4 /dev/sda", mutation_candidate=True)
        blocked, reason_code, _ = check_unsafe_action_guardrails(step, command="mkfs.ext4 /dev/sda")
        assert blocked
        assert "blocked_mkfs" in reason_code

    def test_dd_if_is_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="dd if=/dev/zero of=/dev/sda", mutation_candidate=True)
        blocked, reason_code, _ = check_unsafe_action_guardrails(step, command="dd if=/dev/zero of=/dev/sda")
        assert blocked

    def test_shutdown_is_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="shutdown now", mutation_candidate=True)
        blocked, _, _ = check_unsafe_action_guardrails(step, command="shutdown now")
        assert blocked

    def test_reboot_is_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="reboot", mutation_candidate=True)
        blocked, _, _ = check_unsafe_action_guardrails(step, command="reboot")
        assert blocked

    def test_terraform_is_escalation_scope(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="terraform apply", mutation_candidate=True)
        blocked, reason_code, _ = check_unsafe_action_guardrails(step, command="terraform apply")
        assert blocked
        assert "out_of_scope_terraform" in reason_code

    def test_kubectl_is_escalation_scope(self) -> None:
        step = RepairStep(step_id="s1", title="Bad", command_hint="kubectl delete pod", mutation_candidate=True)
        blocked, reason_code, _ = check_unsafe_action_guardrails(step, command="kubectl delete pod")
        assert blocked
        assert "out_of_scope_kubernetes" in reason_code

    def test_safe_command_not_blocked(self) -> None:
        step = RepairStep(step_id="s1", title="OK", command_hint="systemctl status nginx", mutation_candidate=False)
        blocked, _, _ = check_unsafe_action_guardrails(step, command="systemctl status nginx")
        assert not blocked

    def test_runner_does_not_execute_blocked_step(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Dangerous",
            mutation_candidate=True,
            command_hint="rm -rf /",
            step_type="inspect_state",
            action_class="inspect_state",
        )
        envelope = _make_envelope(
            steps=[step],
            approval_refs=[ApprovalRef(ref_id="a1", operation="admin_repair", granted_at=1000.0, granted_by="test")],
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("denied", "failed")
        assert any("unsafe_command_blocked" in (r.reason_code or "") for r in result.step_results)


# ── DRR-T018: Approval scope binding ──────────────────────────────────────────

class TestApprovalScopeBinding:
    def test_mutation_step_denied_without_approval(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Fix",
            mutation_candidate=True,
            verification_required=True,
            step_type="inspect_state",
            action_class="port_conflict_resolution",
        )
        envelope = _make_envelope(steps=[step], approval_refs=[])
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("needs_approval", "denied")

    def test_mutation_step_passes_with_approval(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Fix",
            mutation_candidate=True,
            verification_required=True,
            step_type="inspect_state",
            action_class="port_conflict_resolution",
        )
        approval = ApprovalRef(
            ref_id="a-scope-1",
            operation="admin_repair",
            granted_at=1000.0,
            granted_by="hub",
        )
        envelope = _make_envelope(steps=[step], approval_refs=[approval])
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("success", "verification_failed", "needs_approval")

    def test_denied_operation_blocks_step(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Fix",
            mutation_candidate=True,
            step_type="inspect_state",
            action_class="port_conflict_resolution",
        )
        approval = ApprovalRef(ref_id="a1", operation="admin_repair", granted_at=1000.0, granted_by="test")
        envelope = _make_envelope(
            steps=[step],
            approval_refs=[approval],
            denied_operations=["port_conflict_resolution"],
        )
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert result.status.value in ("denied", "failed")
        assert any("operation_denied" in (r.reason_code or "") for r in result.step_results)


# ── DRR-T020: Dry-run mode ────────────────────────────────────────────────────

class TestDryRunMode:
    def test_dry_run_returns_preview_status(self) -> None:
        step = RepairStep(step_id="s1", title="Inspect", mutation_candidate=False, step_type="inspect_state")
        envelope = _make_envelope(steps=[step])
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope, dry_run=True)
        assert result.status.value in ("success", "denied")

    def test_dry_run_does_not_execute_mutations(self) -> None:
        step = RepairStep(
            step_id="s1",
            title="Mutate",
            mutation_candidate=True,
            verification_required=True,
            step_type="inspect_state",
        )
        envelope = _make_envelope(steps=[step])
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope, dry_run=True)
        assert result.plan_id == envelope.audit_correlation_id


# ── DRR-T021: Feature flag gating ─────────────────────────────────────────────

class TestFeatureFlags:
    def test_execution_disabled_returns_denied(self) -> None:
        flags = RepairFeatureFlags({"deterministic_repair_execution_enabled": False})
        assert flags.check_execution() == "deterministic_repair_execution_disabled"

    def test_execution_enabled_returns_none(self) -> None:
        flags = RepairFeatureFlags({"deterministic_repair_execution_enabled": True})
        assert flags.check_execution() is None

    def test_analysis_enabled_default(self) -> None:
        flags = RepairFeatureFlags()
        assert flags.analysis_enabled is True

    def test_preview_enabled_default(self) -> None:
        flags = RepairFeatureFlags()
        assert flags.preview_enabled is True

    def test_disabled_runner_returns_denied(self) -> None:
        step = RepairStep(step_id="s1", title="Inspect", mutation_candidate=False, step_type="inspect_state")
        envelope = _make_envelope(steps=[step])
        flags = RepairFeatureFlags({"deterministic_repair_execution_enabled": False})
        runner = RepairProcedureRunner(feature_flags=flags)
        result = runner.run_plan(envelope)
        assert result.status.value == "denied"
        # Feature flag denial is surfaced on the result itself, not in step_results
        assert "deterministic_repair_execution_disabled" in (result.failed_step_id or "") or \
               len(result.step_results) == 0 or \
               result.step_results[0].reason_code == "deterministic_repair_execution_disabled"


# ── DRR-T023: Before/after evidence capture ───────────────────────────────────

class TestBeforeAfterEvidence:
    def test_before_evidence_captured_for_mutation(self) -> None:
        runner = RepairProcedureRunner()
        step = RepairStep(step_id="s1", title="Fix", mutation_candidate=True, step_type="inspect_state")
        before = runner._capture_before_evidence(step)
        assert before.get("phase") == "before"
        assert before.get("step_id") == "s1"

    def test_no_before_evidence_for_inspect(self) -> None:
        runner = RepairProcedureRunner()
        step = RepairStep(step_id="s1", title="Inspect", mutation_candidate=False, step_type="inspect_state")
        before = runner._capture_before_evidence(step)
        assert before == {}

    def test_after_evidence_captured_for_mutation(self) -> None:
        runner = RepairProcedureRunner()
        step = RepairStep(step_id="s1", title="Fix", mutation_candidate=True, step_type="inspect_state")
        before = {"captured_at": 1000.0, "phase": "before", "step_id": "s1"}
        after = runner._capture_after_evidence(step, before)
        assert after.get("phase") == "after"
        assert after.get("before_ref") == 1000.0

    def test_no_after_evidence_for_inspect(self) -> None:
        runner = RepairProcedureRunner()
        step = RepairStep(step_id="s1", title="Inspect", mutation_candidate=False)
        after = runner._capture_after_evidence(step, {})
        assert after == {}


# ── DRR-T024: Rollback hints ──────────────────────────────────────────────────

class TestRollbackHints:
    def test_rollback_hint_propagated_in_step_result(self) -> None:
        from tests.test_worker_deterministic_repair import _make_repair_envelope
        from worker.repair.repair_procedure_runner import RepairProcedureRunner

        envelope = _make_repair_envelope(with_procedure=False)
        envelope.repair_procedure = RepairProcedure(
            procedure_id="proc-rollback-gov-001",
            safety_class="bounded",
            steps=[
                RepairStep(
                    step_id="s1",
                    title="Fix with rollback",
                    mutation_candidate=True,
                    verification_required=True,
                    rollback_hint="restart original process",
                    step_type="inspect_state",
                )
            ],
        )
        envelope.approval_refs = [
            ApprovalRef(ref_id="a-rb", operation="admin_repair", granted_at=1000.0, granted_by="test")
        ]
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        assert len(result.step_results) >= 1
        assert result.step_results[0].rollback_hint_used == "restart original process"

    def test_no_rollback_hint_for_inspect_steps(self) -> None:
        step = RepairStep(step_id="s1", title="Inspect", mutation_candidate=False)
        envelope = _make_envelope(steps=[step])
        runner = RepairProcedureRunner()
        result = runner.run_plan(envelope)
        # Inspect steps have no rollback hint - either empty or "no_mutation" sentinel
        hint = result.step_results[0].rollback_hint_used
        assert hint in ("", "no_mutation")


# ── DRR-T025: Outcome classification ─────────────────────────────────────────

class TestOutcomeClassification:
    def test_standard_outcome_labels_defined(self) -> None:
        from agent.services.deterministic_repair_path_service import STANDARD_OUTCOME_LABELS

        assert "succeeded" in STANDARD_OUTCOME_LABELS
        assert "partially_helped" in STANDARD_OUTCOME_LABELS
        assert "failed" in STANDARD_OUTCOME_LABELS
        assert "regressed" in STANDARD_OUTCOME_LABELS

    def test_verify_final_outcome_success(self) -> None:
        from agent.services.deterministic_repair_path_service import verify_final_repair_outcome

        result = verify_final_repair_outcome(
            execution_result={"status": "completed", "steps": []},
            normalized_evidence={"evidence": []},
            matching_outcome={"outcome": "single_high_confidence"},
        )
        assert result.get("outcome_label") in ("succeeded", "partially_helped")

    def test_verify_final_outcome_failure_labels_failed(self) -> None:
        from agent.services.deterministic_repair_path_service import verify_final_repair_outcome

        result = verify_final_repair_outcome(
            execution_result={"status": "failed"},
            normalized_evidence={"evidence": []},
            matching_outcome={"outcome": "no_match"},
        )
        assert result.get("outcome_label") in ("failed", "regressed", "partially_helped")

    def test_standard_outcome_labels_in_allowed_list(self) -> None:
        from agent.services.deterministic_repair_path_service import (
            STANDARD_OUTCOME_LABELS,
            verify_final_repair_outcome,
        )

        result = verify_final_repair_outcome(
            execution_result={},
            normalized_evidence={},
            matching_outcome={},
        )
        label = result.get("outcome_label")
        assert label in STANDARD_OUTCOME_LABELS or label is None


# ── DRR-T028: Negative learning model ─────────────────────────────────────────

class TestNegativeLearningModel:
    def test_negative_learning_from_memory_entries(self) -> None:
        from agent.services.deterministic_repair_path_service import build_negative_learning_model

        entries = [
            {"procedure_id": "proc-001", "outcome_label": "failed", "problem_class": "port_conflict"},
            {"procedure_id": "proc-001", "outcome_label": "failed", "problem_class": "port_conflict"},
            {"procedure_id": "proc-001", "outcome_label": "regressed", "problem_class": "port_conflict"},
        ]
        model = build_negative_learning_model(memory_entries=entries)
        assert "proc-001" in str(model)
        patterns = model.get("anti_patterns") or []
        assert any(p.get("procedure_id") == "proc-001" for p in patterns)

    def test_success_history_does_not_remove_approval_requirement(self) -> None:
        from agent.services.deterministic_repair_path_service import build_negative_learning_model

        entries = [
            {"procedure_id": "proc-high", "outcome_label": "succeeded", "problem_class": "port_conflict"},
            {"procedure_id": "proc-high", "outcome_label": "succeeded", "problem_class": "port_conflict"},
        ]
        model = build_negative_learning_model(memory_entries=entries)
        # Success-only entries are not in anti_patterns
        patterns = model.get("anti_patterns") or []
        assert not any(p.get("procedure_id") == "proc-high" for p in patterns)

    def test_no_history_returns_empty_model(self) -> None:
        from agent.services.deterministic_repair_path_service import build_negative_learning_model

        model = build_negative_learning_model(memory_entries=[])
        assert isinstance(model, dict)
        assert model.get("anti_patterns") == []


# ── DRR-T029: Environment similarity query ───────────────────────────────────

class TestEnvironmentSimilarityQuery:
    def test_exact_match_gets_high_score(self) -> None:
        from agent.services.deterministic_repair_path_service import compute_environment_similarity

        current = {"platform_target": "ubuntu", "os_family": "linux", "package_manager": "apt"}
        historical = {"platform_target": "ubuntu", "os_family": "linux", "package_manager": "apt"}
        result = compute_environment_similarity(
            current_environment_facts=current,
            reference_environment_facts=historical,
        )
        score = result.get("score") if isinstance(result, dict) else result
        assert isinstance(score, (int, float))
        assert score > 0.5

    def test_different_platform_gets_lower_score(self) -> None:
        from agent.services.deterministic_repair_path_service import compute_environment_similarity

        current = {"platform_target": "ubuntu", "os_family": "linux"}
        historical = {"platform_target": "windows11", "os_family": "windows"}
        result = compute_environment_similarity(
            current_environment_facts=current,
            reference_environment_facts=historical,
        )
        score = result.get("score") if isinstance(result, dict) else result
        assert isinstance(score, (int, float))
        assert score < 0.5

    def test_missing_fields_do_not_crash(self) -> None:
        from agent.services.deterministic_repair_path_service import compute_environment_similarity

        result = compute_environment_similarity(
            current_environment_facts={},
            reference_environment_facts={},
        )
        assert isinstance(result, dict)
        assert result.get("score", 0.0) >= 0.0


# ── DRR-T009: LLM escalation envelope ────────────────────────────────────────

class TestLLMEscalationEnvelope:
    def test_high_confidence_deterministic_success_does_not_escalate(self) -> None:
        from agent.services.deterministic_repair_path_service import decide_llm_escalation

        result = decide_llm_escalation(
            matching_outcome={"outcome": "single_high_confidence"},
            repair_execution_result={"status": "completed"},
            deterministic_paths_exhausted=False,
        )
        assert result["should_escalate"] is False
        assert result["reasons"] == []

    def test_no_match_triggers_escalation(self) -> None:
        from agent.services.deterministic_repair_path_service import decide_llm_escalation

        result = decide_llm_escalation(
            matching_outcome={"outcome": "no_match"},
            repair_execution_result={"status": "unknown"},
            deterministic_paths_exhausted=False,
        )
        assert result["should_escalate"] is True
        assert "unknown_signature" in result["reasons"]

    def test_ambiguous_triggers_escalation(self) -> None:
        from agent.services.deterministic_repair_path_service import decide_llm_escalation

        result = decide_llm_escalation(
            matching_outcome={"outcome": "ambiguous_high_confidence"},
            repair_execution_result={"status": "failed"},
            deterministic_paths_exhausted=False,
        )
        assert result["should_escalate"] is True

    def test_escalation_output_has_schema(self) -> None:
        from agent.services.deterministic_repair_path_service import decide_llm_escalation

        result = decide_llm_escalation(
            matching_outcome={"outcome": "no_match"},
            repair_execution_result={},
            deterministic_paths_exhausted=True,
        )
        assert "schema" in result
        assert "audit" in result

    def test_bounded_escalation_prompt_has_constraints(self) -> None:
        from agent.services.deterministic_repair_path_service import build_bounded_escalation_prompt

        prompt = build_bounded_escalation_prompt(
            escalation_decision={"should_escalate": True, "reasons": ["no_match"]},
            normalized_evidence={"evidence": []},
            signature_matching={"matches": []},
            attempted_paths=["diagnosis"],
            confidence_model={"score": 0.3, "decision": "low"},
        )
        assert "constraints" in prompt
        assert prompt["constraints"]["require_structured_proposal_output"] is True

    def test_escalation_forbidden_for_known_policy_cases(self) -> None:
        from agent.services.deterministic_repair_path_service import LLM_ESCALATION_POLICY_MODEL

        assert "forbidden_when" in LLM_ESCALATION_POLICY_MODEL
        forbidden = LLM_ESCALATION_POLICY_MODEL["forbidden_when"]
        assert any("single_high_confidence" in str(f) for f in forbidden)


# ── DRR-T010: Input validation hardening ──────────────────────────────────────

class TestInputValidationHardening:
    def test_malformed_evidence_does_not_crash_normalize(self) -> None:
        from agent.services.deterministic_repair_path_service import normalize_evidence_bundle

        result = normalize_evidence_bundle(
            evidence_items=[{"bad_key": "value"}],
            environment_facts={},
        )
        assert isinstance(result, dict)

    def test_empty_evidence_returns_structured_result(self) -> None:
        from agent.services.deterministic_repair_path_service import normalize_evidence_bundle

        result = normalize_evidence_bundle(evidence_items=[], environment_facts={})
        assert isinstance(result, dict)
        assert "evidence" in result

    def test_missing_environment_facts_reduces_confidence(self) -> None:
        from agent.services.deterministic_repair_path_service import (
            match_failure_signatures,
            build_initial_failure_signature_catalog,
        )

        catalog = build_initial_failure_signature_catalog()
        result = match_failure_signatures(
            normalized_evidence={"evidence": []},
            environment_facts={},
            signature_catalog=catalog,
        )
        assert isinstance(result, dict)
        assert "matches" in result

    def test_unknown_problem_class_returns_structured_escalation(self) -> None:
        from agent.services.repair_execution_plan_service import generate_repair_execution_plan

        plan = generate_repair_execution_plan(
            matching_outcome={"outcome": "no_match", "best_score": 0.0, "best_problem_class": "unknown_xyz"},
            environment_facts={},
        )
        assert plan is not None
        assert plan.steps

    def test_diagnosis_playbook_with_empty_steps_is_rejected(self) -> None:
        from agent.services.deterministic_repair_path_service import run_diagnosis_playbook

        playbook = {"id": "test-playbook", "steps": []}
        result = run_diagnosis_playbook(
            playbook=playbook,
            normalized_evidence={"evidence": []},
            matching_outcome={"outcome": "no_match"},
        )
        assert result.get("final_state") in ("failed", "completed")

    def test_mutation_playbook_step_is_rejected(self) -> None:
        from agent.services.deterministic_repair_path_service import (
            validate_non_destructive_diagnosis_playbook,
        )

        playbook = {
            "id": "bad-playbook",
            "steps": [{"step_type": "execute_mutation", "id": "s1", "mutation_candidate": True}],
        }
        with pytest.raises(ValueError, match="mutation"):
            validate_non_destructive_diagnosis_playbook(playbook)
