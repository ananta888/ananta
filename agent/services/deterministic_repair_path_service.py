from __future__ import annotations

_SYMBOL_MAP: dict[str, tuple[str, str]] = {
    "LLM_ESCALATION_POLICY_MODEL": ("agent.services._deterministic_repair_constants", "LLM_ESCALATION_POLICY_MODEL"),
    "STANDARD_OUTCOME_LABELS": ("agent.services._deterministic_repair_constants", "STANDARD_OUTCOME_LABELS"),
    "normalize_evidence_bundle": ("agent.services._deterministic_repair_evidence", "normalize_evidence_bundle"),
    "build_deterministic_repair_foundation_snapshot": ("agent.services._deterministic_repair_foundation", "build_deterministic_repair_foundation_snapshot"),
    "build_path_visibility": ("agent.services._deterministic_repair_history", "build_path_visibility"),
    "build_repair_history_inspection_view": ("agent.services._deterministic_repair_history", "build_repair_history_inspection_view"),
    "build_negative_learning_model": ("agent.services._deterministic_repair_learning", "build_negative_learning_model"),
    "compute_environment_similarity": ("agent.services._deterministic_repair_learning", "compute_environment_similarity"),
    "build_bounded_escalation_prompt": ("agent.services._deterministic_repair_llm_escalation", "build_bounded_escalation_prompt"),
    "decide_llm_escalation": ("agent.services._deterministic_repair_llm_escalation", "decide_llm_escalation"),
    "build_operator_proposal_preview": ("agent.services._deterministic_repair_operator_views", "build_operator_proposal_preview"),
    "build_operator_session_summary": ("agent.services._deterministic_repair_operator_views", "build_operator_session_summary"),
    "run_diagnosis_playbook": ("agent.services._deterministic_repair_playbooks", "run_diagnosis_playbook"),
    "convert_llm_proposal_to_reviewed_procedure": ("agent.services._deterministic_repair_procedures", "convert_llm_proposal_to_reviewed_procedure"),
    "execute_repair_procedure": ("agent.services._deterministic_repair_procedures", "execute_repair_procedure"),
    "select_repair_procedure_from_catalog": ("agent.services._deterministic_repair_procedures", "select_repair_procedure_from_catalog"),
    "match_failure_signatures": ("agent.services._deterministic_repair_signatures", "match_failure_signatures"),
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
