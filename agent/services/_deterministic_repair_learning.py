"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Negative-learning model, success-weighted repair recommendations.

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
    ENVIRONMENT_SIMILARITY_MODEL,
)
from agent.services import _deterministic_repair_evidence as _drr_evidence
compute_environment_similarity = _drr_evidence.compute_environment_similarity




log = logging.getLogger(__name__)




def build_negative_learning_model(
    *,
    memory_entries: list[dict[str, Any]],
    min_negative_count: int = 2,
) -> dict[str, Any]:
    negative_counts: dict[str, dict[str, int]] = {}
    for entry in memory_entries:
        procedure_id = str(entry.get("procedure_id") or "unknown_procedure")
        outcome = str(entry.get("outcome_label") or "failed")
        if outcome not in {"failed", "regressed"}:
            continue
        bucket = negative_counts.setdefault(procedure_id, {"failed": 0, "regressed": 0, "total_negative": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1
        bucket["total_negative"] += 1

    anti_patterns: list[dict[str, Any]] = []
    for procedure_id, counts in negative_counts.items():
        if counts["total_negative"] < int(min_negative_count):
            continue
        severity = "high" if counts.get("regressed", 0) > 0 else "medium"
        anti_patterns.append(
            {
                "procedure_id": procedure_id,
                "negative_counts": counts,
                "severity": severity,
                "recommended_action": "block_for_review" if severity == "high" else "deprioritize",
            }
        )
    return {
        "schema": "deterministic_negative_learning_v1",
        "anti_patterns": anti_patterns,
        "tracked_negative_outcomes": ["failed", "regressed"],
        "min_negative_count": int(min_negative_count),
    }





def build_success_weighted_repair_recommendations(
    *,
    repair_catalog: dict[str, Any],
    signature_matching: dict[str, Any],
    current_environment_facts: dict[str, Any],
    memory_entries: list[dict[str, Any]],
    negative_learning_model: dict[str, Any],
    top_k: int = 3,
) -> dict[str, Any]:
    top_matches = list(signature_matching.get("matches") or [])
    match_by_problem_class = {
        str(match.get("problem_class") or ""): float(match.get("score") or 0.0)
        for match in top_matches
    }
    anti_patterns = {
        str(item.get("procedure_id") or ""): item
        for item in list(negative_learning_model.get("anti_patterns") or [])
    }
    ranked: list[dict[str, Any]] = []
    for entry in list(repair_catalog.get("entries") or []):
        procedure = dict(entry.get("procedure") or {})
        procedure_id = str(procedure.get("id") or "")
        problem_class = str(entry.get("problem_class") or "")
        safety_class = str(procedure.get("safety_class") or "safe")
        relevant_history = [item for item in memory_entries if str(item.get("procedure_id") or "") == procedure_id]
        successful = [item for item in relevant_history if str(item.get("outcome_label") or "") == "succeeded"]
        partial = [item for item in relevant_history if str(item.get("outcome_label") or "") == "partially_helped"]
        total_history = len(relevant_history)
        success_rate = ((len(successful) + (len(partial) * 0.5)) / total_history) if total_history > 0 else 0.0

        similarity_scores: list[float] = []
        for history_entry in relevant_history:
            history_env = dict(history_entry.get("environment_facts") or {})
            similarity = compute_environment_similarity(
                current_environment_facts=current_environment_facts,
                reference_environment_facts=history_env,
            )
            similarity_scores.append(float(similarity["score"]))
        similarity_score = (sum(similarity_scores) / len(similarity_scores)) if similarity_scores else 0.0
        signature_score = match_by_problem_class.get(problem_class, 0.0)

        anti_pattern = anti_patterns.get(procedure_id)
        negative_penalty = 0.0
        blocked_by_negative_learning = False
        if anti_pattern:
            severity = str(anti_pattern.get("severity") or "medium")
            negative_penalty = 0.6 if severity == "high" else 0.25
            blocked_by_negative_learning = severity == "high"

        weighted_score = (signature_score * 0.55) + (success_rate * 0.3) + (similarity_score * 0.15) - negative_penalty
        bounded_score = round(max(0.0, min(1.0, weighted_score)), 3)
        requires_approval = safety_class in {"review_first", "high_risk"}

        ranked.append(
            {
                "procedure_id": procedure_id,
                "problem_class": problem_class,
                "safety_class": safety_class,
                "weighted_score": bounded_score,
                "score_components": {
                    "signature_score": round(signature_score, 3),
                    "success_rate": round(success_rate, 3),
                    "environment_similarity": round(similarity_score, 3),
                    "negative_penalty": round(negative_penalty, 3),
                },
                "requires_approval": requires_approval,
                "blocked_by_negative_learning": blocked_by_negative_learning,
                "safety_override": requires_approval or blocked_by_negative_learning,
                "explanation": (
                    "Ranking combines signature match, historical success and environment similarity; "
                    "safety and negative-learning guardrails remain enforced."
                ),
            }
        )
    ranked.sort(key=lambda item: (-float(item["weighted_score"]), item["procedure_id"]))
    return {
        "schema": "deterministic_success_weighted_recommendation_v1",
        "ranked_recommendations": ranked[: max(1, int(top_k))],
        "safety_override_rule": "ranking_never_overrides_approval_or_negative_learning_blocks",
    }



