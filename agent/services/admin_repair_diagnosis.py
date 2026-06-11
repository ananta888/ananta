from __future__ import annotations

from typing import Any

from agent.services.admin_repair_taxonomy import PROBLEM_TAXONOMY


def _problem_match_score(problem_class: str, text: str) -> int:
    keyword_hits = 0
    for keyword in PROBLEM_TAXONOMY[problem_class]["keywords"]:
        if keyword in text:
            keyword_hits += 1
    return keyword_hits


def _classify_problem(symptom: str, targets: list[str]) -> tuple[str, float]:
    text = " ".join([symptom.strip().lower(), " ".join(targets).lower()]).strip()
    if not text:
        return "service_health", 0.4

    scores = {problem_class: _problem_match_score(problem_class, text) for problem_class in PROBLEM_TAXONOMY}
    best_class = max(scores, key=scores.get)
    best_score = scores[best_class]
    if best_score <= 0:
        return "service_health", 0.45
    confidence = min(0.92, 0.55 + (best_score * 0.12))
    return best_class, round(confidence, 2)


def _build_diagnosis_artifact(
    *,
    issue_symptom: str,
    affected_targets: list[str],
    evidence_sources: list[str],
) -> dict[str, Any]:
    problem_class, confidence = _classify_problem(issue_symptom, affected_targets)
    taxonomy = PROBLEM_TAXONOMY[problem_class]
    return {
        "schema": "admin_repair_diagnosis_v1",
        "problem_class": problem_class,
        "confidence": confidence,
        "likely_causes": list(taxonomy["cause_hints"]),
        "evidence_sources": list(evidence_sources),
        "evidence_links": [
            {"source": source, "reference": f"evidence:{source}", "collection_mode": "bounded"}
            for source in evidence_sources
        ],
        "next_steps": [
            "review_environment_summary",
            "validate_selected_evidence_sources",
            "confirm_bounded_repair_plan_or_keep_diagnosis_only",
        ],
    }
