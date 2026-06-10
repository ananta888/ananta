"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Path visibility and history inspection rendering.

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




def build_path_visibility(
    *,
    llm_escalation_decision: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    if bool(llm_escalation_decision.get("should_escalate")):
        path_type = "llm_escalated"
    elif str(matching_outcome.get("outcome") or "") == "ambiguous_high_confidence":
        path_type = "mixed"
    else:
        path_type = "deterministic"
    return {
        "schema": "deterministic_path_visibility_v1",
        "path_type": path_type,
        "matching_outcome": matching_outcome.get("outcome"),
        "escalation_reasons": list(llm_escalation_decision.get("reasons") or []),
        "operator_visible": True,
    }





def build_repair_history_inspection_view(
    *,
    memory_entries: list[dict[str, Any]],
    filter_problem_class: str | None = None,
    filter_platform_target: str | None = None,
) -> dict[str, Any]:
    filtered = []
    for entry in memory_entries:
        problem_class = str(entry.get("problem_class") or "")
        platform_target = str((entry.get("environment_facts") or {}).get("platform_target") or "")
        if filter_problem_class and problem_class != filter_problem_class:
            continue
        if filter_platform_target and platform_target != filter_platform_target:
            continue
        filtered.append(
            {
                "procedure_id": entry.get("procedure_id"),
                "problem_class": problem_class,
                "platform_target": platform_target,
                "outcome_label": entry.get("outcome_label"),
                "signature_id": entry.get("signature_id"),
            }
        )
    return {
        "schema": "deterministic_repair_history_inspection_v1",
        "entries": filtered,
        "filters": {
            "problem_class": filter_problem_class,
            "platform_target": filter_platform_target,
        },
    }



