"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Repair audit chain construction.

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




def build_repair_audit_chain(
    *,
    diagnosis_run: dict[str, Any],
    matching_outcome: dict[str, Any],
    repair_execution_result: dict[str, Any],
    final_verification: dict[str, Any],
    llm_escalation_decision: dict[str, Any],
) -> dict[str, Any]:
    events = [
        {
            "event": "diagnosis_completed",
            "playbook_id": diagnosis_run.get("playbook_id"),
            "classification": diagnosis_run.get("classification"),
            "state": diagnosis_run.get("final_state"),
        },
        {
            "event": "matching_outcome",
            "outcome": matching_outcome.get("outcome"),
            "best_problem_class": matching_outcome.get("best_problem_class"),
            "best_score": matching_outcome.get("best_score"),
        },
        {
            "event": "repair_execution",
            "procedure_id": repair_execution_result.get("procedure_id"),
            "status": repair_execution_result.get("status"),
            "stop_reason": repair_execution_result.get("stop_reason"),
        },
        {
            "event": "verification_completed",
            "outcome_label": final_verification.get("outcome_label"),
            "execution_status": (final_verification.get("verification_summary") or {}).get("execution_status"),
        },
        {
            "event": "escalation_decision",
            "should_escalate": llm_escalation_decision.get("should_escalate"),
            "reasons": llm_escalation_decision.get("reasons"),
        },
    ]
    return {
        "schema": "deterministic_repair_audit_chain_v1",
        "events": events,
        "traceability": {
            "deterministic_used": not bool(llm_escalation_decision.get("should_escalate")),
            "llm_escalation_used": bool(llm_escalation_decision.get("should_escalate")),
        },
    }



