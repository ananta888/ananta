"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Repair confidence evaluation.

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
    CONFIDENCE_THRESHOLDS,
)

log = logging.getLogger(__name__)




def evaluate_repair_confidence(
    *,
    signature_strength: float,
    platform_match: float,
    history_success_rate: float,
) -> dict[str, Any]:
    normalized_signature = min(1.0, max(0.0, float(signature_strength)))
    normalized_platform = min(1.0, max(0.0, float(platform_match)))
    normalized_history = min(1.0, max(0.0, float(history_success_rate)))
    score = round((normalized_signature * 0.5) + (normalized_platform * 0.25) + (normalized_history * 0.25), 3)
    if score >= CONFIDENCE_THRESHOLDS["deterministic_execute"]:
        decision = "deterministic_execute"
    elif score >= CONFIDENCE_THRESHOLDS["review_required"]:
        decision = "review_required"
    else:
        decision = "llm_escalation"
    return {
        "score": score,
        "decision": decision,
        "components": {
            "signature_strength": normalized_signature,
            "platform_match": normalized_platform,
            "history_success_rate": normalized_history,
        },
        "thresholds": dict(CONFIDENCE_THRESHOLDS),
    }



