"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Failure signature construction, matching and explanation. Maintains the catalog of well-known failure patterns.

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
    REPAIR_PROBLEM_CLASS_INVENTORY,
    INITIAL_SIGNATURE_CATALOG_DEFINITIONS,
)


from dataclasses import dataclass, field
from agent.services import _deterministic_repair_utils as _drr_utils
_match_failure_signatures_impl = _drr_utils._match_failure_signatures_impl


@dataclass
class FailureSignature:
    id: str
    problem_class: str
    evidence_patterns: tuple[str, ...]
    structured_fields: tuple[str, ...] = ()
    environment_constraints: dict[str, str] = field(default_factory=dict)
    confidence_weight: float = 1.0

    def compiled_patterns(self) -> tuple[Pattern[str], ...]:
        compiled: list[Pattern[str]] = []
        for pattern in self.evidence_patterns:
            compiled.append(re.compile(pattern, flags=re.IGNORECASE))
        return tuple(compiled)


log = logging.getLogger(__name__)




def build_failure_signature(payload: dict[str, Any]) -> FailureSignature:
    try:
        raw_patterns = payload.get("evidence_patterns") or []
        patterns = tuple(str(item).strip() for item in raw_patterns if str(item).strip())
        if not patterns:
            raise ValueError("failure_signature_requires_patterns")
        problem_class = str(payload.get("problem_class") or "").strip()
        if problem_class not in REPAIR_PROBLEM_CLASS_INVENTORY:
            raise ValueError("failure_signature_problem_class_unknown")
        confidence_weight = float(payload.get("confidence_weight", 1.0))
        confidence_weight = min(2.0, max(0.1, confidence_weight))
        return FailureSignature(
            id=str(payload.get("id") or "").strip() or "signature-unnamed",
            problem_class=problem_class,
            evidence_patterns=patterns,
            structured_fields=tuple(str(item).strip() for item in payload.get("structured_fields", []) if str(item).strip()),
            environment_constraints={str(k): str(v) for k, v in dict(payload.get("environment_constraints") or {}).items()},
            confidence_weight=confidence_weight,
        )
    except Exception as exc:
        log.error("build_failure_signature failed: %s", exc)
        raise





def signature_to_dict(signature: FailureSignature) -> dict[str, Any]:
    return {
        "id": signature.id,
        "problem_class": signature.problem_class,
        "evidence_patterns": list(signature.evidence_patterns),
        "structured_fields": list(signature.structured_fields),
        "environment_constraints": dict(signature.environment_constraints),
        "confidence_weight": signature.confidence_weight,
    }





def build_initial_failure_signature_catalog() -> tuple[FailureSignature, ...]:
    catalog: list[FailureSignature] = []
    for payload in INITIAL_SIGNATURE_CATALOG_DEFINITIONS:
        catalog.append(build_failure_signature(payload))
    return tuple(catalog)





def match_failure_signatures(
    *,
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    signature_catalog: tuple[FailureSignature, ...] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    try:
        return _match_failure_signatures_impl(
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
            signature_catalog=signature_catalog,
            top_k=top_k,
        )
    except Exception as exc:
        log.error("match_failure_signatures failed: %s", exc)
        return {
            "schema": "deterministic_signature_matching_v1",
            "match_count": 0,
            "matches": [],
            "llm_used": False,
            "error": str(exc),
        }





def classify_signature_matching_outcome(
    *,
    ranked_matches: list[dict[str, Any]],
    confidence_model: dict[str, Any],
    ambiguity_delta: float = 0.08,
) -> dict[str, Any]:
    high_threshold = float(confidence_model.get("thresholds", {}).get("deterministic_execute", 0.78))
    review_threshold = float(confidence_model.get("thresholds", {}).get("review_required", 0.55))
    if not ranked_matches:
        return {
            "outcome": "no_match",
            "decision": "llm_escalation",
            "best_problem_class": None,
            "best_score": 0.0,
            "requires_review": True,
            "requires_llm_escalation": True,
            "recommended_next_steps": [
                "collect_additional_bounded_evidence",
                "run_fallback_diagnosis_playbook",
                "escalate_to_llm_with_bounded_context",
            ],
        }
    best = ranked_matches[0]
    best_score = float(best.get("score") or 0.0)
    second_score = float(ranked_matches[1].get("score") or 0.0) if len(ranked_matches) > 1 else 0.0
    is_ambiguous = len(ranked_matches) > 1 and abs(best_score - second_score) <= float(ambiguity_delta)
    if best_score < review_threshold:
        return {
            "outcome": "low_confidence",
            "decision": "deterministic_fallback_then_llm_if_needed",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": True,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "collect_corroborating_signals",
                "run_non_destructive_diagnosis_playbook",
                "avoid_mutation_until_confidence_improves",
            ],
        }
    if is_ambiguous:
        return {
            "outcome": "ambiguous_high_confidence",
            "decision": "review_required_before_mutation",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": True,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "run_branching_diagnosis_playbook",
                "collect_targeted_disambiguation_evidence",
                "present_top_signatures_for_operator_review",
            ],
        }
    if best_score >= high_threshold:
        return {
            "outcome": "single_high_confidence",
            "decision": "deterministic_repair_candidate",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": False,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "execute_deterministic_diagnosis_playbook",
                "prepare_bounded_repair_procedure_preview",
                "verify_before_and_after_each_mutation",
            ],
        }
    return {
        "outcome": "low_confidence",
        "decision": "review_required",
        "best_problem_class": best.get("problem_class"),
        "best_score": best_score,
        "requires_review": True,
        "requires_llm_escalation": False,
        "recommended_next_steps": [
            "collect_corroborating_signals",
            "run_non_destructive_diagnosis_playbook",
            "avoid_mutation_until_confidence_improves",
        ],
    }





def build_signature_explanation(
    *,
    match: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    matched_patterns = list(match.get("matched_patterns") or [])
    evidence_snippets: list[str] = []
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        message = str(raw.get("message") or entry.get("summary") or "").strip()
        lowered = message.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in matched_patterns):
            evidence_snippets.append(message[:180])
        if len(evidence_snippets) >= 3:
            break
    platform_target = str(environment_facts.get("platform_target") or "unknown")
    return {
        "signature_id": match.get("signature_id"),
        "problem_class": match.get("problem_class"),
        "score": match.get("score"),
        "matched_patterns": matched_patterns,
        "matched_environment_constraints": list(match.get("matched_environment_constraints") or []),
        "key_evidence": evidence_snippets,
        "summary": (
            f"Matched {match.get('signature_id')} ({match.get('problem_class')}) with score {match.get('score')} "
            f"on platform {platform_target} using patterns: {', '.join(matched_patterns[:2])}."
        ),
    }



