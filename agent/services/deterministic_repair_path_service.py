from __future__ import annotations

_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "APPROVAL_REQUIREMENT_MODEL": ("agent.services._deterministic_repair_constants", "APPROVAL_REQUIREMENT_MODEL"),
    "build_bounded_escalation_prompt": ("agent.services._deterministic_repair_llm_escalation", "build_bounded_escalation_prompt"),
    "build_deterministic_repair_foundation_snapshot": ("agent.services._deterministic_repair_foundation", "build_deterministic_repair_foundation_snapshot"),
    "build_failure_signature": ("agent.services._deterministic_repair_signatures", "build_failure_signature"),
    "build_initial_failure_signature_catalog": ("agent.services._deterministic_repair_signatures", "build_initial_failure_signature_catalog"),
    "build_initial_repair_procedure_catalog": ("agent.services._deterministic_repair_procedures", "build_initial_repair_procedure_catalog"),
    "build_negative_learning_model": ("agent.services._deterministic_repair_learning", "build_negative_learning_model"),
    "build_operator_proposal_preview": ("agent.services._deterministic_repair_operator_views", "build_operator_proposal_preview"),
    "build_operator_session_summary": ("agent.services._deterministic_repair_operator_views", "build_operator_session_summary"),
    "build_path_visibility": ("agent.services._deterministic_repair_history", "build_path_visibility"),
    "build_recovery_hint_bundle": ("agent.services._deterministic_repair_misc", "build_recovery_hint_bundle"),
    "build_repair_audit_chain": ("agent.services._deterministic_repair_audit", "build_repair_audit_chain"),
    "build_repair_history_inspection_view": ("agent.services._deterministic_repair_history", "build_repair_history_inspection_view"),
    "build_repair_outcome_memory_entry": ("agent.services._deterministic_repair_outcome_memory", "build_repair_outcome_memory_entry"),
    "build_repair_procedure_preview": ("agent.services._deterministic_repair_foundation", "build_repair_procedure_preview"),
    "build_repair_procedure_template": ("agent.services._deterministic_repair_procedures", "build_repair_procedure_template"),
    "build_rollout_plan": ("agent.services._deterministic_repair_knowledge", "build_rollout_plan"),
    "build_signature_explanation": ("agent.services._deterministic_repair_signatures", "build_signature_explanation"),
    "build_success_weighted_repair_recommendations": ("agent.services._deterministic_repair_learning", "build_success_weighted_repair_recommendations"),
    "build_test_coverage_manifest": ("agent.services._deterministic_repair_knowledge", "build_test_coverage_manifest"),
    "capture_command_result": ("agent.services._deterministic_repair_evidence", "capture_command_result"),
    "classify_signature_matching_outcome": ("agent.services._deterministic_repair_signatures", "classify_signature_matching_outcome"),
    "collect_environment_facts": ("agent.services._deterministic_repair_evidence", "collect_environment_facts"),
    "compute_environment_similarity": ("agent.services._deterministic_repair_learning", "compute_environment_similarity"),
    "convert_llm_proposal_to_reviewed_procedure": ("agent.services._deterministic_repair_procedures", "convert_llm_proposal_to_reviewed_procedure"),
    "curate_escalation_feedback": ("agent.services._deterministic_repair_llm_escalation", "curate_escalation_feedback"),
    "decide_llm_escalation": ("agent.services._deterministic_repair_llm_escalation", "decide_llm_escalation"),
    "DIAGNOSIS_PROCEDURE_MODEL": ("agent.services._deterministic_repair_constants", "DIAGNOSIS_PROCEDURE_MODEL"),
    "ENVIRONMENT_SIMILARITY_MODEL": ("agent.services._deterministic_repair_constants", "ENVIRONMENT_SIMILARITY_MODEL"),
    "evaluate_repair_confidence": ("agent.services._deterministic_repair_confidence", "evaluate_repair_confidence"),
    "evaluate_unsafe_action_guardrails": ("agent.services._deterministic_repair_misc", "evaluate_unsafe_action_guardrails"),
    "execute_repair_procedure": ("agent.services._deterministic_repair_procedures", "execute_repair_procedure"),
    "FailureSignature": ("agent.services._deterministic_repair_signatures", "FailureSignature"),
    "get_initial_diagnosis_playbooks": ("agent.services._deterministic_repair_playbooks", "get_initial_diagnosis_playbooks"),
    "ingest_structured_logs": ("agent.services._deterministic_repair_evidence", "ingest_structured_logs"),
    "LLM_ESCALATION_POLICY_MODEL": ("agent.services._deterministic_repair_constants", "LLM_ESCALATION_POLICY_MODEL"),
    "match_failure_signatures": ("agent.services._deterministic_repair_signatures", "match_failure_signatures"),
    "normalize_evidence_bundle": ("agent.services._deterministic_repair_evidence", "normalize_evidence_bundle"),
    "OPERATOR_GUIDE_METADATA": ("agent.services._deterministic_repair_constants", "OPERATOR_GUIDE_METADATA"),
    "OPERATOR_VIEW_MODEL": ("agent.services._deterministic_repair_constants", "OPERATOR_VIEW_MODEL"),
    "REPAIR_ACTION_SAFETY_CLASSES": ("agent.services._deterministic_repair_constants", "REPAIR_ACTION_SAFETY_CLASSES"),
    "REPAIR_EXECUTION_SAFETY_POLICY": ("agent.services._deterministic_repair_constants", "REPAIR_EXECUTION_SAFETY_POLICY"),
    "REPAIR_OUTCOME_MEMORY_MODEL": ("agent.services._deterministic_repair_constants", "REPAIR_OUTCOME_MEMORY_MODEL"),
    "REPAIR_PATH_TARGET_MODEL": ("agent.services._deterministic_repair_constants", "REPAIR_PATH_TARGET_MODEL"),
    "REPAIR_PROBLEM_CLASS_INVENTORY": ("agent.services._deterministic_repair_constants", "REPAIR_PROBLEM_CLASS_INVENTORY"),
    "REPAIR_PROCEDURE_MODEL": ("agent.services._deterministic_repair_constants", "REPAIR_PROCEDURE_MODEL"),
    "REPAIR_STATE_MODEL": ("agent.services._deterministic_repair_constants", "REPAIR_STATE_MODEL"),
    "REPAIR_VERIFICATION_MODEL": ("agent.services._deterministic_repair_constants", "REPAIR_VERIFICATION_MODEL"),
    "ROLLOUT_PLAN_MODEL": ("agent.services._deterministic_repair_constants", "ROLLOUT_PLAN_MODEL"),
    "run_diagnosis_playbook": ("agent.services._deterministic_repair_playbooks", "run_diagnosis_playbook"),
    "run_step_verification": ("agent.services._deterministic_repair_procedures", "run_step_verification"),
    "select_repair_procedure_from_catalog": ("agent.services._deterministic_repair_procedures", "select_repair_procedure_from_catalog"),
    "signature_to_dict": ("agent.services._deterministic_repair_signatures", "signature_to_dict"),
    "STANDARD_OUTCOME_LABELS": ("agent.services._deterministic_repair_constants", "STANDARD_OUTCOME_LABELS"),
    "track_repair_outcomes": ("agent.services._deterministic_repair_outcome_memory", "track_repair_outcomes"),
    "validate_non_destructive_diagnosis_playbook": ("agent.services._deterministic_repair_playbooks", "validate_non_destructive_diagnosis_playbook"),
    "verify_final_repair_outcome": ("agent.services._deterministic_repair_outcome_memory", "verify_final_repair_outcome"),
}


def __getattr__(name: str):
    if name in _SYMBOL_MAP:
        import importlib
        module_path, attr = _SYMBOL_MAP[name]
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)
    raise AttributeError(f"module 'agent.services.deterministic_repair_path_service' has no attribute {name!r}")


__all__ = list(_SYMBOL_MAP)
