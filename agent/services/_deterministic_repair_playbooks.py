"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Diagnosis playbook loading, validation and execution. Non-destructive environment probing that surfaces symptoms before any repair.

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
    INITIAL_DIAGNOSIS_PLAYBOOKS,
)

log = logging.getLogger(__name__)




def get_initial_diagnosis_playbooks() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(INITIAL_DIAGNOSIS_PLAYBOOKS)





def validate_non_destructive_diagnosis_playbook(playbook: dict[str, Any]) -> None:
    mutating_step_types = {"execute_mutation", "apply_fix", "restart_service", "run_repair"}
    for step in list(playbook.get("steps") or []):
        step_type = str(step.get("step_type") or "").strip()
        if bool(step.get("mutation_candidate")):
            raise ValueError("diagnosis_playbook_contains_mutation_candidate")
        if step_type in mutating_step_types:
            raise ValueError("diagnosis_playbook_contains_mutating_step_type")





def run_diagnosis_playbook(
    *,
    playbook: dict[str, Any],
    normalized_evidence: dict[str, Any],
    matching_outcome: dict[str, Any],
    max_steps: int = 20,
) -> dict[str, Any]:
    validate_non_destructive_diagnosis_playbook(playbook)
    steps = list(playbook.get("steps") or [])
    step_map = {str(step.get("id") or ""): step for step in steps}
    if not steps:
        return {
            "schema": "deterministic_diagnosis_run_v1",
            "playbook_id": playbook.get("id"),
            "executed_steps": [],
            "state_updates": [],
            "final_state": "failed",
            "classification": None,
            "non_destructive_enforced": True,
        }

    current_step_id = str(playbook.get("entry_step_id") or steps[0]["id"])
    visited: set[str] = set()
    executed_steps: list[dict[str, Any]] = []
    state_updates: list[dict[str, Any]] = []
    classification = None
    final_state = "running"
    stopped_early = False

    for _ in range(max_steps):
        if not current_step_id:
            final_state = "completed"
            break
        if current_step_id in visited:
            final_state = "failed_loop_detected"
            state_updates.append({"event": "loop_detected", "step_id": current_step_id})
            break
        visited.add(current_step_id)
        step = step_map.get(current_step_id)
        if not step:
            final_state = "failed_missing_step"
            state_updates.append({"event": "missing_step", "step_id": current_step_id})
            break

        step_type = str(step.get("step_type") or "")
        executed_steps.append(
            {
                "step_id": current_step_id,
                "step_type": step_type,
                "title": step.get("title"),
            }
        )

        if step_type == "collect_evidence":
            expected_sources = list(step.get("evidence_sources") or [])
            available_sources = {
                str((dict(item.get("raw") or {})).get("source") or item.get("source") or "")
                for item in list(normalized_evidence.get("evidence") or [])
            }
            collected_sources = [source for source in expected_sources if source in available_sources]
            state_updates.append(
                {
                    "event": "evidence_collected",
                    "step_id": current_step_id,
                    "expected_sources": expected_sources,
                    "collected_sources": collected_sources,
                    "missing_sources": [source for source in expected_sources if source not in collected_sources],
                }
            )
            current_step_id = str(step.get("next_step") or "")
            continue

        if step_type == "evaluate_signature_outcome":
            outcome = str(matching_outcome.get("outcome") or "no_match")
            next_by_outcome = dict(step.get("next_by_outcome") or {})
            current_step_id = str(next_by_outcome.get(outcome) or step.get("next_step") or "")
            stop_when = set(step.get("stop_when") or [])
            state_updates.append(
                {
                    "event": "signature_outcome_evaluated",
                    "step_id": current_step_id or step.get("id"),
                    "outcome": outcome,
                    "stop_condition_met": outcome in stop_when,
                }
            )
            if outcome in stop_when and current_step_id:
                stopped_early = True
            continue

        if step_type == "branch":
            outcome = str(matching_outcome.get("outcome") or "no_match")
            branches = dict(step.get("branches") or {})
            fallback_step = str(step.get("fallback_step") or "")
            selected_step = str(branches.get(outcome) or fallback_step)
            state_updates.append(
                {
                    "event": "branch_selected",
                    "step_id": step.get("id"),
                    "outcome": outcome,
                    "selected_step": selected_step,
                    "fallback_used": selected_step == fallback_step,
                }
            )
            current_step_id = selected_step
            continue

        if step_type == "classify_case":
            classification = str(step.get("classification") or "unknown_or_mixed_failure")
            state_updates.append(
                {
                    "event": "classification_emitted",
                    "step_id": step.get("id"),
                    "classification": classification,
                    "confidence_band": step.get("classification_confidence"),
                }
            )
            if bool(step.get("stop")):
                final_state = "classified"
                break
            current_step_id = str(step.get("next_step") or "")
            continue

        if step_type == "stop":
            final_state = "stopped"
            state_updates.append({"event": "stop", "step_id": step.get("id"), "reason": step.get("reason")})
            break

        raise ValueError(f"diagnosis_playbook_unknown_step_type:{step_type}")

    else:
        final_state = "max_steps_reached"

    if final_state == "running":
        final_state = "completed"
    return {
        "schema": "deterministic_diagnosis_run_v1",
        "playbook_id": playbook.get("id"),
        "executed_steps": executed_steps,
        "state_updates": state_updates,
        "final_state": final_state,
        "classification": classification,
        "matching_outcome": matching_outcome.get("outcome"),
        "non_destructive_enforced": True,
        "stopped_early": stopped_early,
    }



