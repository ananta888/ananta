"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Repair action safety classification, approval-scope derivation, unsafe-action guardrail evaluation.

Public re-exports: the public agent.services.deterministic_repair_path_service
module continues to expose every function via thin delegating wrappers, so
existing imports keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def classify_repair_action_safety(*, step: dict[str, Any], procedure_safety_class: str) -> str:
    if not bool(step.get("mutation_candidate")):
        return "inspect_only"
    if procedure_safety_class == "high_risk":
        return "high_risk"
    if procedure_safety_class == "review_first":
        return "confirm_required"
    return "bounded_low_risk"
