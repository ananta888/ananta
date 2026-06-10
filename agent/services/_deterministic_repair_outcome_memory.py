"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Final outcome verification, outcome-memory persistence/load, outcome tracking and history snapshot.

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
    STANDARD_OUTCOME_LABELS,
)

log = logging.getLogger(__name__)




def verify_final_repair_outcome(
    *,
    execution_result: dict[str, Any],
    normalized_evidence: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    step_results = list(execution_result.get("steps") or [])
    statuses = [str((step.get("verification") or {}).get("status") or "needs_review") for step in step_results]
    has_fail = "fail" in statuses
    has_review = "needs_review" in statuses or "warning" in statuses
    contradictory = bool(execution_result.get("contradictory_evidence_detected"))
    worsening = bool(execution_result.get("worsening_signals_detected"))
    status = str(execution_result.get("status") or "aborted")
    if worsening:
        outcome_label = "regressed"
    elif status == "completed" and not has_fail and not contradictory and not has_review:
        outcome_label = "succeeded"
    elif status in {"completed", "preview_only"} and not has_fail:
        outcome_label = "partially_helped"
    else:
        outcome_label = "failed"
    return {
        "schema": "deterministic_repair_final_verification_v1",
        "outcome_label": outcome_label,
        "allowed_outcome_labels": list(STANDARD_OUTCOME_LABELS),
        "problem_class": execution_result.get("problem_class"),
        "matching_outcome": matching_outcome.get("outcome"),
        "evidence_based": True,
        "verification_summary": {
            "step_verification_statuses": statuses,
            "contradictory_evidence": contradictory,
            "worsening_signals": worsening,
            "execution_status": status,
        },
    }





def build_repair_outcome_memory_entry(
    *,
    signature_matching: dict[str, Any],
    selected_catalog_entry: dict[str, Any],
    environment_facts: dict[str, Any],
    execution_result: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    top_match = (list(signature_matching.get("matches") or [{}]) or [{}])[0]
    return {
        "schema": "deterministic_repair_outcome_memory_entry_v1",
        "signature_id": top_match.get("signature_id"),
        "problem_class": selected_catalog_entry.get("problem_class"),
        "environment_facts": {
            "platform_target": environment_facts.get("platform_target"),
            "os_family": environment_facts.get("os_family"),
            "package_manager": environment_facts.get("package_manager"),
            "service_state": environment_facts.get("service_state"),
        },
        "procedure_id": execution_result.get("procedure_id"),
        "execution_status": execution_result.get("status"),
        "outcome_label": final_verification.get("outcome_label"),
        "verification_evidence": final_verification.get("verification_summary"),
    }





def persist_repair_outcome_memory(entry: dict[str, Any]) -> dict[str, Any]:
    try:
        db_entry = RepairOutcomeMemoryDB(
            signature_id=str(entry.get("signature_id") or ""),
            problem_class=str(entry.get("problem_class") or ""),
            environment_facts=dict(entry.get("environment_facts") or {}),
            procedure_id=str(entry.get("procedure_id") or ""),
            execution_status=str(entry.get("execution_status") or ""),
            outcome_label=str(entry.get("outcome_label") or ""),
            verification_evidence=dict(entry.get("verification_evidence") or {}),
        )
        saved = get_repair_outcome_memory_repo().save(db_entry)
        return {"persisted": True, "id": saved.id}
    except Exception as exc:
        log.error("Failed to persist repair outcome memory: %s", exc)
        return {"persisted": False, "error": str(exc)}





def load_repair_outcome_memory(
    *,
    problem_class: str | None = None,
    platform_target: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    try:
        if platform_target:
            entries = get_repair_outcome_memory_repo().find(
                problem_class=problem_class,
                platform_target=platform_target,
                limit=limit,
            )
        elif problem_class:
            entries = get_repair_outcome_memory_repo().find(problem_class=problem_class, limit=limit)
        else:
            entries = get_repair_outcome_memory_repo().find_all(limit=limit)
        return [
            {
                "signature_id": e.signature_id,
                "problem_class": e.problem_class,
                "environment_facts": dict(e.environment_facts or {}),
                "procedure_id": e.procedure_id,
                "execution_status": e.execution_status,
                "outcome_label": e.outcome_label,
                "verification_evidence": dict(e.verification_evidence or {}),
                "created_at": e.created_at,
            }
            for e in entries
        ]
    except Exception as exc:
        log.error("Failed to load repair outcome memory: %s", exc)
        return []





def track_repair_outcomes(memory_entries: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {label: 0 for label in STANDARD_OUTCOME_LABELS}
    for entry in memory_entries:
        label = str(entry.get("outcome_label") or "failed")
        if label not in counts:
            counts[label] = 0
        counts[label] += 1
    total = sum(counts.values())
    recommendation_score = 0.0
    if total > 0:
        recommendation_score = (
            (counts.get("succeeded", 0) * 1.0)
            + (counts.get("partially_helped", 0) * 0.4)
            - (counts.get("failed", 0) * 0.5)
            - (counts.get("regressed", 0) * 1.0)
        ) / total
    return {
        "schema": "deterministic_repair_outcome_tracking_v1",
        "allowed_outcome_labels": list(STANDARD_OUTCOME_LABELS),
        "counts_by_outcome": counts,
        "total": total,
        "recommendation_score": round(recommendation_score, 3),
    }



