"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Operator session summary, proposal preview, history inspection view, path visibility, recovery hints.

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

log = logging.getLogger(__name__)




def build_operator_session_summary(
    *,
    diagnosis_run: dict[str, Any],
    matching_outcome: dict[str, Any],
    repair_execution_result: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "deterministic_operator_session_summary_v1",
        "current_state": final_verification.get("outcome_label"),
        "detected_signature_class": matching_outcome.get("best_problem_class"),
        "chosen_path": {
            "diagnosis_playbook": diagnosis_run.get("playbook_id"),
            "procedure_id": repair_execution_result.get("procedure_id"),
            "execution_status": repair_execution_result.get("status"),
        },
        "verification_status": final_verification.get("verification_summary"),
        "compact_view": True,
    }





def build_operator_proposal_preview(
    *,
    repair_preview: dict[str, Any],
    selected_catalog_entry: dict[str, Any],
) -> dict[str, Any]:
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    steps = list(procedure.get("steps") or [])
    preview_steps = [
        {
            "id": step.get("id"),
            "title": step.get("title"),
            "mutation_candidate": bool(step.get("mutation_candidate")),
            "expected_verification": "step_verification_required",
        }
        for step in steps
    ]
    return {
        "schema": "deterministic_operator_proposal_preview_v1",
        "procedure_id": repair_preview.get("procedure_id"),
        "problem_class": repair_preview.get("problem_class"),
        "steps": preview_steps,
        "approval_decision_ready": True,
        "compact_view": True,
    }



