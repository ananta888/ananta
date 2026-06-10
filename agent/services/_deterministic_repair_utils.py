"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Low-level helpers: evidence extraction, environment/structured-field matching, severity detection, approval-scope keys, LLM-proposal structuring/conversion.

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
from agent.services import _deterministic_repair_operator_views as _drr_operator_views
build_operator_proposal_preview = _drr_operator_views.build_operator_proposal_preview
build_operator_session_summary = _drr_operator_views.build_operator_session_summary
from agent.services import _deterministic_repair_audit as _drr_audit
build_repair_audit_chain = _drr_audit.build_repair_audit_chain
from agent.services import _deterministic_repair_signatures as _drr_signatures
build_initial_failure_signature_catalog = _drr_signatures.build_initial_failure_signature_catalog
build_signature_explanation = _drr_signatures.build_signature_explanation
classify_signature_matching_outcome = _drr_signatures.classify_signature_matching_outcome
match_failure_signatures = _drr_signatures.match_failure_signatures
signature_to_dict = _drr_signatures.signature_to_dict
from agent.services import _deterministic_repair_outcome_memory as _drr_outcome_memory
build_repair_outcome_memory_entry = _drr_outcome_memory.build_repair_outcome_memory_entry
track_repair_outcomes = _drr_outcome_memory.track_repair_outcomes
verify_final_repair_outcome = _drr_outcome_memory.verify_final_repair_outcome
from agent.services import _deterministic_repair_evidence as _drr_evidence
collect_environment_facts = _drr_evidence.collect_environment_facts
compute_environment_similarity = _drr_evidence.compute_environment_similarity
ingest_structured_logs = _drr_evidence.ingest_structured_logs
normalize_evidence_bundle = _drr_evidence.normalize_evidence_bundle
from agent.services import _deterministic_repair_procedures as _drr_procedures
build_initial_repair_procedure_catalog = _drr_procedures.build_initial_repair_procedure_catalog
build_repair_procedure_preview = _drr_procedures.build_repair_procedure_preview
build_repair_procedure_template = _drr_procedures.build_repair_procedure_template
convert_llm_proposal_to_reviewed_procedure = _drr_procedures.convert_llm_proposal_to_reviewed_procedure
execute_repair_procedure = _drr_procedures.execute_repair_procedure
select_repair_procedure_from_catalog = _drr_procedures.select_repair_procedure_from_catalog
from agent.services import _deterministic_repair_history as _drr_history
build_path_visibility = _drr_history.build_path_visibility
build_repair_history_inspection_view = _drr_history.build_repair_history_inspection_view
from agent.services import _deterministic_repair_playbooks as _drr_playbooks
get_initial_diagnosis_playbooks = _drr_playbooks.get_initial_diagnosis_playbooks
run_diagnosis_playbook = _drr_playbooks.run_diagnosis_playbook
from agent.services import _deterministic_repair_learning as _drr_learning
build_negative_learning_model = _drr_learning.build_negative_learning_model
build_success_weighted_repair_recommendations = _drr_learning.build_success_weighted_repair_recommendations
from agent.services import _deterministic_repair_llm_escalation as _drr_llm_escalation
build_bounded_escalation_prompt = _drr_llm_escalation.build_bounded_escalation_prompt
curate_escalation_feedback = _drr_llm_escalation.curate_escalation_feedback
decide_llm_escalation = _drr_llm_escalation.decide_llm_escalation
from agent.services import _deterministic_repair_misc as _drr_misc
build_recovery_hint_bundle = _drr_misc.build_recovery_hint_bundle
evaluate_unsafe_action_guardrails = _drr_misc.evaluate_unsafe_action_guardrails
from agent.services import _deterministic_repair_confidence as _drr_confidence
evaluate_repair_confidence = _drr_confidence.evaluate_repair_confidence
from agent.services import _deterministic_repair_knowledge as _drr_knowledge
build_golden_path_examples = _drr_knowledge.build_golden_path_examples
build_rollout_plan = _drr_knowledge.build_rollout_plan
build_test_coverage_manifest = _drr_knowledge.build_test_coverage_manifest


log = logging.getLogger(__name__)




def _extract_evidence_text(normalized_evidence: dict[str, Any]) -> str:
    chunks: list[str] = []
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        chunks.extend(
            [
                str(entry.get("summary") or ""),
                str(raw.get("message") or ""),
                str(raw.get("stderr") or ""),
                str(raw.get("stdout") or ""),
                str(raw.get("command") or ""),
                str(raw.get("health_check") or ""),
            ]
        )
    return "\n".join(piece for piece in chunks if piece).lower()





def _evaluate_environment_match(signature: FailureSignature, environment_facts: dict[str, Any]) -> dict[str, Any]:
    constraints = dict(signature.environment_constraints or {})
    if not constraints:
        return {
            "score": 1.0,
            "matched_constraints": [],
            "missing_constraints": [],
        }
    matched: list[str] = []
    missing: list[str] = []
    for key, expected in constraints.items():
        actual = str(environment_facts.get(key) or "").strip().lower()
        if actual == str(expected).strip().lower():
            matched.append(key)
        else:
            missing.append(key)
    score = len(matched) / len(constraints)
    return {
        "score": round(score, 3),
        "matched_constraints": matched,
        "missing_constraints": missing,
    }





def _evaluate_structured_field_match(signature: FailureSignature, normalized_evidence: dict[str, Any]) -> dict[str, Any]:
    fields = list(signature.structured_fields or [])
    if not fields:
        return {"score": 1.0, "matched_fields": []}
    available_fields: set[str] = set()
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        available_fields.update(str(key) for key in raw.keys())
    matched_fields = [field for field in fields if field in available_fields]
    score = len(matched_fields) / len(fields)
    return {"score": round(score, 3), "matched_fields": matched_fields}





def _match_failure_signatures_impl(
    *,
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    signature_catalog: tuple[FailureSignature, ...] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    catalog = signature_catalog or build_initial_failure_signature_catalog()
    evidence_text = _extract_evidence_text(normalized_evidence)
    matches: list[dict[str, Any]] = []
    for signature in catalog:
        compiled = signature.compiled_patterns()
        matched_patterns = [pattern.pattern for pattern in compiled if pattern.search(evidence_text)]
        if not matched_patterns:
            continue
        unmatched_patterns = [pattern.pattern for pattern in compiled if pattern.pattern not in matched_patterns]
        pattern_strength = len(matched_patterns) / len(compiled)
        environment_match = _evaluate_environment_match(signature, environment_facts)
        structured_match = _evaluate_structured_field_match(signature, normalized_evidence)
        weighted_score = (
            (pattern_strength * 0.7)
            + (environment_match["score"] * 0.2)
            + (structured_match["score"] * 0.1)
        ) * signature.confidence_weight
        score = round(min(1.0, weighted_score), 3)
        matches.append(
            {
                "signature_id": signature.id,
                "problem_class": signature.problem_class,
                "score": score,
                "confidence_weight": signature.confidence_weight,
                "signature_strength": round(pattern_strength, 3),
                "matched_patterns": matched_patterns,
                "unmatched_patterns": unmatched_patterns,
                "environment_match": environment_match["score"],
                "matched_environment_constraints": environment_match["matched_constraints"],
                "missing_environment_constraints": environment_match["missing_constraints"],
                "structured_field_match": structured_match["score"],
                "matched_structured_fields": structured_match["matched_fields"],
            }
        )
    ranked = sorted(matches, key=lambda item: (-float(item["score"]), item["signature_id"]))[: max(1, int(top_k))]
    return {
        "schema": "deterministic_signature_matching_v1",
        "match_count": len(ranked),
        "matches": ranked,
        "llm_used": False,
    }





def _detect_contradictory_evidence(normalized_evidence: dict[str, Any]) -> bool:
    text = _extract_evidence_text(normalized_evidence)
    has_healthy = bool(re.search(r"\b(healthy|running|ok|resolved)\b", text))
    has_failure = bool(re.search(r"\b(failed|error|denied|panic|unhealthy)\b", text))
    return has_healthy and has_failure





def _detect_worsening_signals(normalized_evidence: dict[str, Any]) -> bool:
    text = _extract_evidence_text(normalized_evidence)
    return bool(re.search(r"\b(regressed|worse|panic|fatal|crash loop)\b", text))





def _approval_scope_key(*, procedure_id: str, target_scope: str, session_id: str) -> str:
    return f"{procedure_id}|{target_scope}|{session_id}"





def _structure_llm_proposal(
    llm_data: dict[str, Any],
    llm_proposal: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    proposal_steps = list(llm_data.get("steps") or [])
    structured_steps: list[dict[str, Any]] = []
    for index, step in enumerate(proposal_steps[:5], start=1):
        if isinstance(step, dict):
            step_id = str(step.get("id") or f"llm-proposal-step-{index:02d}")
            title = str(step.get("title") or "")[:180]
            mutation = bool(step.get("mutation_candidate", True))
            review = bool(step.get("requires_review", True))
            approval = bool(step.get("requires_approval", True))
            allowed = bool(step.get("execution_allowed", False))
        else:
            step_id = f"llm-proposal-step-{index:02d}"
            title = str(step).strip()[:180]
            mutation = True
            review = True
            approval = True
            allowed = False
        if not title:
            continue
        structured_steps.append({
            "id": step_id,
            "title": title,
            "mutation_candidate": mutation,
            "requires_review": review,
            "requires_approval": approval,
            "execution_allowed": allowed,
        })
    return _build_llm_conversion_result(
        structured_steps=structured_steps,
        llm_proposal=llm_proposal,
        environment_facts=environment_facts,
    )





def _default_llm_proposal_conversion(
    llm_proposal: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    proposal_steps = list(llm_proposal.get("steps") or [])
    structured_steps: list[dict[str, Any]] = []
    for index, step in enumerate(proposal_steps[:5], start=1):
        text = str(step).strip()
        if not text:
            continue
        structured_steps.append({
            "id": f"llm-proposal-step-{index:02d}",
            "title": text[:180],
            "mutation_candidate": True,
            "requires_review": True,
            "requires_approval": True,
            "execution_allowed": False,
        })
    return _build_llm_conversion_result(
        structured_steps=structured_steps or [{
            "id": "llm-proposal-step-01",
            "title": "No concrete step supplied; requires operator curation.",
            "mutation_candidate": False,
            "requires_review": True,
            "requires_approval": True,
            "execution_allowed": False,
        }],
        llm_proposal=llm_proposal,
        environment_facts=environment_facts,
    )





def _build_llm_conversion_result(
    *,
    structured_steps: list[dict[str, Any]],
    llm_proposal: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "deterministic_llm_proposal_conversion_v1",
        "proposal_id": str(llm_proposal.get("proposal_id") or "llm-proposal-unknown"),
        "platform_target": environment_facts.get("platform_target"),
        "review_required": any(s.get("requires_review", True) for s in structured_steps),
        "approval_required": any(s.get("requires_approval", True) for s in structured_steps),
        "execution_allowed_without_review": all(s.get("execution_allowed", False) for s in structured_steps),
        "structured_candidate_procedure": {
            "id": f"reviewed-{str(llm_proposal.get('proposal_id') or 'candidate')}",
            "source": "llm_escalation",
            "steps": structured_steps,
        },
    }





def _detect_severity(message: str) -> str:
    text = str(message or "").lower()
    for severity, pattern in SEVERITY_PATTERNS:
        if re.search(pattern, text):
            return severity
    return "info"





