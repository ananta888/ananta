"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Miscellaneous utilities that don't fit a single theme.

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
    UNSAFE_ACTION_GUARDRAIL_MODEL,
)

log = logging.getLogger(__name__)




def build_recovery_hint_bundle(
    *,
    selected_catalog_entry: dict[str, Any],
    execution_result: dict[str, Any],
) -> dict[str, Any]:
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    rollback_hints = list(procedure.get("rollback_hints") or [])
    non_reversible = [step.get("step_id") for step in list(execution_result.get("steps") or []) if bool(step.get("mutation_candidate"))]
    return {
        "schema": "deterministic_repair_recovery_hints_v1",
        "procedure_id": procedure.get("id"),
        "rollback_hints": rollback_hints,
        "manual_recovery_required": bool(non_reversible),
        "non_reversible_step_ids": non_reversible,
        "linked_execution_status": execution_result.get("status"),
    }





def evaluate_unsafe_action_guardrails(
    *,
    proposed_actions: list[str],
) -> dict[str, Any]:
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    allowed_actions: list[str] = []
    blocked_patterns = [re.compile(pattern, flags=re.IGNORECASE) for pattern in UNSAFE_ACTION_GUARDRAIL_MODEL["blocked_patterns"]]
    out_of_scope_patterns = [re.compile(pattern, flags=re.IGNORECASE) for pattern in UNSAFE_ACTION_GUARDRAIL_MODEL["out_of_scope_patterns"]]
    for action in proposed_actions:
        text = str(action or "").strip()
        if not text:
            continue
        blocked_match = next((pattern.pattern for pattern in blocked_patterns if pattern.search(text)), None)
        if blocked_match:
            blocked.append(
                {
                    "action": text,
                    "reason": "blocked_pattern",
                    "matched_pattern": blocked_match,
                    "severity": "critical",
                }
            )
            continue
        out_of_scope_match = next((pattern.pattern for pattern in out_of_scope_patterns if pattern.search(text)), None)
        if out_of_scope_match:
            blocked.append(
                {
                    "action": text,
                    "reason": "out_of_scope",
                    "matched_pattern": out_of_scope_match,
                    "severity": "high",
                }
            )
            continue
        if len(text) > 180:
            warnings.append(
                {
                    "action": text[:180],
                    "reason": "action_text_truncated_for_review",
                }
            )
        allowed_actions.append(text)
    return {
        "schema": "deterministic_unsafe_action_guardrail_evaluation_v1",
        "blocked_actions": blocked,
        "warnings": warnings,
        "allowed_actions": allowed_actions,
        "fail_closed": bool(UNSAFE_ACTION_GUARDRAIL_MODEL.get("fail_closed", True)),
    }



