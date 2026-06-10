"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Bounded LLM escalation: decision, prompt construction, proposal conversion, feedback curation.

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
    LLM_ESCALATION_POLICY_MODEL,
)

log = logging.getLogger(__name__)




def decide_llm_escalation(
    *,
    matching_outcome: dict[str, Any],
    repair_execution_result: dict[str, Any],
    deterministic_paths_exhausted: bool,
) -> dict[str, Any]:
    outcome = str(matching_outcome.get("outcome") or "no_match")
    execution_status = str(repair_execution_result.get("status") or "unknown")
    contradictory = bool(repair_execution_result.get("contradictory_evidence_detected"))
    reasons: list[str] = []
    if outcome == "no_match":
        reasons.append("unknown_signature")
    if outcome == "ambiguous_high_confidence":
        reasons.append("ambiguous_high_confidence")
    if outcome == "low_confidence":
        reasons.append("low_confidence")
    if contradictory:
        reasons.append("contradictory_evidence")
    if deterministic_paths_exhausted:
        reasons.append("exhausted_deterministic_paths")
    if outcome == "single_high_confidence" and execution_status == "completed" and not contradictory:
        reasons = []
    should_escalate = bool(reasons)
    return {
        "schema": "deterministic_llm_escalation_decision_v1",
        "should_escalate": should_escalate,
        "reasons": reasons,
        "matching_outcome": outcome,
        "execution_status": execution_status,
        "audit": {
            "policy_schema": LLM_ESCALATION_POLICY_MODEL["schema"],
            "allowed_reasons": list(LLM_ESCALATION_POLICY_MODEL["allowed_reasons"]),
            "forbidden_when": list(LLM_ESCALATION_POLICY_MODEL["forbidden_when"]),
        },
    }





def build_bounded_escalation_prompt(
    *,
    escalation_decision: dict[str, Any],
    normalized_evidence: dict[str, Any],
    signature_matching: dict[str, Any],
    attempted_paths: list[str],
    confidence_model: dict[str, Any],
) -> dict[str, Any]:
    bounded_evidence = []
    for entry in list(normalized_evidence.get("evidence") or [])[:6]:
        bounded_evidence.append(
            {
                "type": entry.get("type"),
                "source": entry.get("source"),
                "severity": entry.get("severity"),
                "summary": str(entry.get("summary") or "")[:200],
            }
        )
    top_matches = [
        {
            "signature_id": match.get("signature_id"),
            "problem_class": match.get("problem_class"),
            "score": match.get("score"),
        }
        for match in list(signature_matching.get("matches") or [])[:3]
    ]
    return {
        "schema": "deterministic_bounded_llm_escalation_prompt_v1",
        "enabled": bool(escalation_decision.get("should_escalate")),
        "reasons": list(escalation_decision.get("reasons") or []),
        "known_evidence": bounded_evidence,
        "attempted_paths": list(attempted_paths or []),
        "confidence": {
            "score": confidence_model.get("score"),
            "decision": confidence_model.get("decision"),
            "thresholds": confidence_model.get("thresholds"),
        },
        "top_signature_matches": top_matches,
        "constraints": {
            "max_evidence_items": 6,
            "max_chars_per_item": 200,
            "require_structured_proposal_output": True,
        },
    }





def curate_escalation_feedback(
    *,
    escalation_decision: dict[str, Any],
    proposal_conversion: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    should_curate = bool(escalation_decision.get("should_escalate"))
    outcome_label = str(final_verification.get("outcome_label") or "failed")
    candidates: list[dict[str, Any]] = []
    if should_curate:
        candidates.append(
            {
                "candidate_type": "procedure",
                "source_proposal_id": proposal_conversion.get("proposal_id"),
                "curation_required": True,
                "target_catalog": "deterministic_repair_catalog_v1",
                "outcome_label": outcome_label,
            }
        )
        candidates.append(
            {
                "candidate_type": "signature",
                "source_proposal_id": proposal_conversion.get("proposal_id"),
                "curation_required": True,
                "target_catalog": "deterministic_failure_signature_catalog_v1",
                "outcome_label": outcome_label,
            }
        )
    return {
        "schema": "deterministic_escalation_feedback_curation_v1",
        "should_curate": should_curate,
        "candidates": candidates,
        "explicit_curation_step_required": True,
    }



