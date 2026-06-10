"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Deterministic repair foundation snapshot builder (the public entry point).

Public re-exports: the public agent.services.deterministic_repair_path_service
module continues to expose every function via thin delegating wrappers, so
existing imports keep working unchanged.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, Pattern

from agent.db_models import RepairOutcomeMemoryDB
from agent.repositories.repair_outcome import get_repair_outcome_memory_repo

from agent.services._deterministic_repair_constants import (
    REPAIR_PATH_TARGET_MODEL,
    REPAIR_PROBLEM_CLASS_INVENTORY,
    REPAIR_STATE_MODEL,
    ALLOWED_EVIDENCE_TYPES,
    SEVERITY_PATTERNS,
    DIAGNOSIS_PROCEDURE_MODEL,
    REPAIR_PROCEDURE_MODEL,
    REPAIR_VERIFICATION_MODEL,
    REPAIR_OUTCOME_MEMORY_MODEL,
    ENVIRONMENT_SIMILARITY_MODEL,
    REPAIR_ACTION_SAFETY_CLASSES,
    APPROVAL_REQUIREMENT_MODEL,
    LLM_ESCALATION_POLICY_MODEL,
    OPERATOR_VIEW_MODEL,
    OPERATOR_GUIDE_METADATA,
    ROLLOUT_PLAN_MODEL,
    TEST_COVERAGE_MODEL,
)
from agent.services import _deterministic_repair_procedures as _drr_procedures
from agent.services._deterministic_repair_evidence import (
    collect_environment_facts,
    ingest_structured_logs,
    normalize_evidence_bundle,
)
from agent.services._deterministic_repair_signatures import (
    classify_signature_matching_outcome,
    build_signature_explanation,
    build_initial_failure_signature_catalog,
    match_failure_signatures,
)
from agent.services._deterministic_repair_confidence import evaluate_repair_confidence
from agent.services._deterministic_repair_playbooks import get_initial_diagnosis_playbooks
from agent.services._deterministic_repair_playbooks import run_diagnosis_playbook
from agent.services._deterministic_repair_procedures import build_repair_procedure_template
from agent.services._deterministic_repair_procedures import build_initial_repair_procedure_catalog
from agent.services._deterministic_repair_procedures import select_repair_procedure_from_catalog
from agent.services._deterministic_repair_procedures import _approval_scope_key
from agent.services._deterministic_repair_procedures import execute_repair_procedure
from agent.services._deterministic_repair_outcome_memory import verify_final_repair_outcome
from agent.services._deterministic_repair_misc import build_recovery_hint_bundle
from agent.services._deterministic_repair_outcome_memory import build_repair_outcome_memory_entry
from agent.services._deterministic_repair_outcome_memory import track_repair_outcomes
from agent.services._deterministic_repair_evidence import compute_environment_similarity
from agent.services._deterministic_repair_learning import build_negative_learning_model
from agent.services._deterministic_repair_learning import build_success_weighted_repair_recommendations
from agent.services._deterministic_repair_llm_escalation import decide_llm_escalation
from agent.services._deterministic_repair_llm_escalation import build_bounded_escalation_prompt
from agent.services._deterministic_repair_procedures import convert_llm_proposal_to_reviewed_procedure
from agent.services._deterministic_repair_llm_escalation import curate_escalation_feedback
from agent.services._deterministic_repair_audit import build_repair_audit_chain
from agent.services._deterministic_repair_operator_views import build_operator_session_summary
from agent.services._deterministic_repair_history import build_path_visibility
from agent.services._deterministic_repair_operator_views import build_operator_proposal_preview
from agent.services._deterministic_repair_history import build_repair_history_inspection_view
from agent.services._deterministic_repair_knowledge import build_golden_path_examples
from agent.services._deterministic_repair_knowledge import build_rollout_plan
from agent.services._deterministic_repair_knowledge import build_test_coverage_manifest
from agent.services._deterministic_repair_misc import evaluate_unsafe_action_guardrails
from agent.services._deterministic_repair_signatures import signature_to_dict
build_repair_procedure_preview = _drr_procedures.build_repair_procedure_preview





log = logging.getLogger(__name__)


def build_deterministic_repair_foundation_snapshot(
    *,
    mode_data: dict[str, Any],
    issue_symptom: str,
    evidence_sources: list[str],
) -> dict[str, Any]:
    try:
        return _build_deterministic_repair_foundation_snapshot_impl_local(
            mode_data=mode_data,
            issue_symptom=issue_symptom,
            evidence_sources=evidence_sources,
        )
    except Exception as exc:
        log.error("Failed to build deterministic repair foundation snapshot: %s", exc)
        # Use the procedure preview to return a structured error artifact
        repair_preview = _drr_procedures.build_repair_procedure_preview(final_plan_json={})
        return {
            "error": "deterministic_repair_foundation_failed",
            "detail": str(exc),
            "repair_procedure": None,
            "repair_preview": None,
            "diagnosis_artifact": None,
        }



def _build_deterministic_repair_foundation_snapshot_impl_local(
    *,
    mode_data: dict[str, Any],
    issue_symptom: str,
    evidence_sources: list[str],
) -> dict[str, Any]:
    execution_scope = str(mode_data.get("execution_scope") or "diagnosis_only").strip().lower()
    environment_facts = collect_environment_facts(mode_data)
    synthetic_logs = ingest_structured_logs(
        [
            {
                "message": issue_symptom,
                "severity": "error",
                "source": "issue_symptom",
            }
        ],
        source="issue_symptom",
    )
    evidence_items: list[dict[str, Any]] = []
    evidence_items.extend(synthetic_logs)
    evidence_items.append(
        {
            "type": "environment_fact",
            "source": "environment",
            "severity": "info",
            "message": f"platform={environment_facts.get('platform_target')}",
        }
    )
    for source in evidence_sources:
        evidence_items.append(
            {
                "type": "service_status" if source == "service_status" else "health_check",
                "source": source,
                "severity": "info",
                "message": f"source_enabled:{source}",
            }
        )

    normalized_evidence = normalize_evidence_bundle(
        evidence_items=evidence_items,
        environment_facts=environment_facts,
    )
    confidence = evaluate_repair_confidence(
        signature_strength=0.7,
        platform_match=1.0 if environment_facts.get("platform_target") in {"windows11", "ubuntu"} else 0.35,
        history_success_rate=0.5,
    )
    signature_catalog = build_initial_failure_signature_catalog()
    signature_matching = match_failure_signatures(
        normalized_evidence=normalized_evidence,
        environment_facts=environment_facts,
        signature_catalog=signature_catalog,
    )
    matching_outcome = classify_signature_matching_outcome(
        ranked_matches=list(signature_matching.get("matches") or []),
        confidence_model=confidence,
    )
    signature_explanations = [
        build_signature_explanation(
            match=match,
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
        )
        for match in list(signature_matching.get("matches") or [])[:3]
    ]
    diagnosis_playbooks = get_initial_diagnosis_playbooks()
    selected_problem_class = str(
        matching_outcome.get("best_problem_class")
        or "service_start_failure"
    )
    selected_playbook = diagnosis_playbooks.get(selected_problem_class) or diagnosis_playbooks["service_start_failure"]
    diagnosis_run = run_diagnosis_playbook(
        playbook=selected_playbook,
        normalized_evidence=normalized_evidence,
        matching_outcome=matching_outcome,
    )
    repair_procedure_template = build_repair_procedure_template(
        problem_class=selected_problem_class,
        platform_target=str(environment_facts.get("platform_target") or "unknown"),
    )
    repair_catalog = build_initial_repair_procedure_catalog(signature_catalog=signature_catalog)
    selected_catalog_entry = select_repair_procedure_from_catalog(
        repair_catalog=repair_catalog,
        matching_outcome=matching_outcome,
    )
    repair_preview = build_repair_procedure_preview(
        selected_catalog_entry=selected_catalog_entry,
        matching_outcome=matching_outcome,
    )
    execution_session_id = "deterministic-repair-session-v1"
    execution_target_scope = "service_runtime"
    approval_scope = _approval_scope_key(
        procedure_id=str((selected_catalog_entry.get("procedure") or {}).get("id") or "unknown_procedure"),
        target_scope=execution_target_scope,
        session_id=execution_session_id,
    )
    repair_execution_dry_run = execute_repair_procedure(
        selected_catalog_entry=selected_catalog_entry,
        normalized_evidence=normalized_evidence,
        environment_facts=environment_facts,
        dry_run=True,
        approval_policy={"approved_mutations": False},
        session_id=execution_session_id,
        target_scope=execution_target_scope,
    )
    if execution_scope == "diagnosis_only":
        repair_execution_apply = {
            "schema": "deterministic_repair_execution_v1",
            "procedure_id": str((selected_catalog_entry.get("procedure") or {}).get("id") or "unknown_procedure"),
            "problem_class": selected_catalog_entry.get("problem_class"),
            "status": "skipped_diagnosis_only",
            "stop_reason": "execution_scope_diagnosis_only",
            "steps": [],
            "abort_conditions": [],
            "contradictory_evidence_detected": False,
            "worsening_signals_detected": False,
        }
    else:
        repair_execution_apply = execute_repair_procedure(
            selected_catalog_entry=selected_catalog_entry,
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
            dry_run=False,
            approval_policy={
                "approved_mutations": True,
                "approved_scopes": [approval_scope],
            },
            session_id=execution_session_id,
            target_scope=execution_target_scope,
        )
    final_verification = verify_final_repair_outcome(
        execution_result=repair_execution_apply,
        normalized_evidence=normalized_evidence,
        matching_outcome=matching_outcome,
    )
    recovery_hints = build_recovery_hint_bundle(
        selected_catalog_entry=selected_catalog_entry,
        execution_result=repair_execution_apply,
    )
    memory_entry = build_repair_outcome_memory_entry(
        signature_matching=signature_matching,
        selected_catalog_entry=selected_catalog_entry,
        environment_facts=environment_facts,
        execution_result=repair_execution_apply,
        final_verification=final_verification,
    )
    outcome_tracking = track_repair_outcomes([memory_entry])
    environment_similarity = compute_environment_similarity(
        current_environment_facts=environment_facts,
        reference_environment_facts=dict(memory_entry.get("environment_facts") or {}),
    )
    negative_learning_model = build_negative_learning_model(memory_entries=[memory_entry])
    success_weighted_recommendations = build_success_weighted_repair_recommendations(
        repair_catalog=repair_catalog,
        signature_matching=signature_matching,
        current_environment_facts=environment_facts,
        memory_entries=[memory_entry],
        negative_learning_model=negative_learning_model,
    )
    deterministic_paths_exhausted = (
        matching_outcome.get("outcome") in {"no_match", "low_confidence", "ambiguous_high_confidence"}
        and str(repair_execution_apply.get("status") or "") != "completed"
    )
    llm_escalation_decision = decide_llm_escalation(
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        deterministic_paths_exhausted=bool(deterministic_paths_exhausted),
    )
    llm_escalation_prompt = build_bounded_escalation_prompt(
        escalation_decision=llm_escalation_decision,
        normalized_evidence=normalized_evidence,
        signature_matching=signature_matching,
        attempted_paths=[
            str(diagnosis_run.get("playbook_id") or ""),
            str((selected_catalog_entry.get("procedure") or {}).get("id") or ""),
        ],
        confidence_model=confidence,
    )
    llm_proposal_conversion = convert_llm_proposal_to_reviewed_procedure(
        llm_proposal={
            "proposal_id": "llm-repair-proposal-v1",
            "steps": [
                "Collect additional bounded evidence for ambiguous branch",
                "Prepare reviewed repair candidate with explicit approval gate",
            ],
        },
        environment_facts=environment_facts,
    )
    escalation_feedback_curation = curate_escalation_feedback(
        escalation_decision=llm_escalation_decision,
        proposal_conversion=llm_proposal_conversion,
        final_verification=final_verification,
    )
    repair_audit_chain = build_repair_audit_chain(
        diagnosis_run=diagnosis_run,
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        final_verification=final_verification,
        llm_escalation_decision=llm_escalation_decision,
    )
    operator_session_summary = build_operator_session_summary(
        diagnosis_run=diagnosis_run,
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        final_verification=final_verification,
    )
    path_visibility = build_path_visibility(
        llm_escalation_decision=llm_escalation_decision,
        matching_outcome=matching_outcome,
    )
    operator_proposal_preview = build_operator_proposal_preview(
        repair_preview=repair_preview,
        selected_catalog_entry=selected_catalog_entry,
    )
    repair_history_view = build_repair_history_inspection_view(
        memory_entries=[memory_entry],
        filter_problem_class=selected_problem_class,
        filter_platform_target=(str(environment_facts.get("platform_target")) if environment_facts.get("platform_target") else None),
    )
    golden_path_examples = build_golden_path_examples()
    rollout_plan = build_rollout_plan()
    test_coverage_manifest = build_test_coverage_manifest()
    unsafe_action_guardrails = evaluate_unsafe_action_guardrails(
        proposed_actions=[
            str(step.get("title") or "")
            for step in list((selected_catalog_entry.get("procedure") or {}).get("steps") or [])
        ]
        + [str(step.get("title") or "") for step in list((llm_proposal_conversion.get("structured_candidate_procedure") or {}).get("steps") or [])],
    )
    return {
        "target_model": dict(REPAIR_PATH_TARGET_MODEL),
        "problem_class_inventory": dict(REPAIR_PROBLEM_CLASS_INVENTORY),
        "state_model": dict(REPAIR_STATE_MODEL),
        "confidence_model": confidence,
        "evidence_ingestion_model": {
            "allowed_evidence_types": list(ALLOWED_EVIDENCE_TYPES),
            "selected_sources": list(evidence_sources),
            "bounded_collection": {
                "max_sources": 8,
                "max_log_entries_per_source": 200,
                "max_chars_per_entry": 4000,
            },
        },
        "environment_facts": environment_facts,
        "normalized_evidence": normalized_evidence,
        "failure_signature_model": {
            "schema": "failure_signature_v1",
            "fields": [
                "id",
                "problem_class",
                "evidence_patterns",
                "structured_fields",
                "environment_constraints",
                "confidence_weight",
            ],
        },
        "initial_signature_catalog": {
            "schema": "deterministic_failure_signature_catalog_v1",
            "entries": [signature_to_dict(signature) for signature in signature_catalog],
        },
        "signature_matching": signature_matching,
        "signature_matching_outcome": matching_outcome,
        "signature_explanations": signature_explanations,
        "diagnosis_procedure_model": dict(DIAGNOSIS_PROCEDURE_MODEL),
        "diagnosis_playbooks": {
            "schema": "deterministic_diagnosis_playbook_catalog_v1",
            "entries": list(diagnosis_playbooks.values()),
        },
        "diagnosis_run": diagnosis_run,
        "non_destructive_diagnosis_policy": {
            "enforced": True,
            "rule": "no_mutation_candidate_steps_in_diagnosis_playbooks",
        },
        "repair_procedure_model": dict(REPAIR_PROCEDURE_MODEL),
        "repair_procedure_template": repair_procedure_template,
        "repair_catalog": repair_catalog,
        "selected_repair_catalog_entry": selected_catalog_entry,
        "repair_preview": repair_preview,
        "repair_execution": {
            "dry_run": repair_execution_dry_run,
            "apply_run": repair_execution_apply,
        },
        "verification_model": dict(REPAIR_VERIFICATION_MODEL),
        "final_repair_verification": final_verification,
        "recovery_hints": recovery_hints,
        "outcome_memory_model": dict(REPAIR_OUTCOME_MEMORY_MODEL),
        "outcome_memory_entry": memory_entry,
        "outcome_tracking": outcome_tracking,
        "environment_similarity_model": dict(ENVIRONMENT_SIMILARITY_MODEL),
        "environment_similarity": environment_similarity,
        "success_weighted_recommendations": success_weighted_recommendations,
        "negative_learning_model": negative_learning_model,
        "repair_action_safety_classes": dict(REPAIR_ACTION_SAFETY_CLASSES),
        "approval_requirement_model": dict(APPROVAL_REQUIREMENT_MODEL),
        "bounded_execution_policy": {
            "schema": "deterministic_bounded_execution_policy_v1",
            "allow_unbounded_actions": False,
            "allow_unknown_actions": False,
            "enforced": True,
        },
        "llm_escalation_policy": dict(LLM_ESCALATION_POLICY_MODEL),
        "llm_escalation_decision": llm_escalation_decision,
        "llm_escalation_prompt": llm_escalation_prompt,
        "llm_proposal_conversion": llm_proposal_conversion,
        "escalation_feedback_curation": escalation_feedback_curation,
        "repair_audit_chain": repair_audit_chain,
        "unsafe_action_guardrails": unsafe_action_guardrails,
        "operator_views_model": dict(OPERATOR_VIEW_MODEL),
        "operator_session_summary": operator_session_summary,
        "path_visibility": path_visibility,
        "operator_proposal_preview": operator_proposal_preview,
        "repair_history_view": repair_history_view,
        "test_coverage_model": dict(TEST_COVERAGE_MODEL),
        "test_coverage_manifest": test_coverage_manifest,
        "operator_guide_metadata": dict(OPERATOR_GUIDE_METADATA),
        "golden_path_examples": golden_path_examples,
        "rollout_plan_model": dict(ROLLOUT_PLAN_MODEL),
        "rollout_plan": rollout_plan,
        "repair_procedure": dict(selected_catalog_entry.get("procedure") or {}),
        "diagnosis_artifact": dict(mode_data.get("diagnosis_artifact") or {
            "problem_class": selected_problem_class,
            "confidence": matching_outcome.get("best_score", 0.0),
            "likely_causes": [],
        }),
    }

