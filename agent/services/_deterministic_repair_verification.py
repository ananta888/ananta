"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Step-level verification helpers used by the procedure runner.

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
from agent.services import _deterministic_repair_utils as _drr_utils
_detect_contradictory_evidence = _drr_utils._detect_contradictory_evidence
_detect_worsening_signals = _drr_utils._detect_worsening_signals
_extract_evidence_text = _drr_utils._extract_evidence_text




log = logging.getLogger(__name__)




def run_step_verification(
    *,
    step: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    evidence_text = _extract_evidence_text(normalized_evidence)
    contradictory = _detect_contradictory_evidence(normalized_evidence)
    worsening = _detect_worsening_signals(normalized_evidence)
    requires_strict = bool(step.get("mutation_candidate"))
    platform_target = str(environment_facts.get("platform_target") or "unknown")
    has_failure_signals = bool(re.search(r"\b(failed|error|denied|unhealthy)\b", evidence_text))
    status = "pass"
    if worsening:
        status = "fail"
    elif contradictory:
        status = "warning"
    elif requires_strict and has_failure_signals:
        status = "needs_review"
    return {
        "schema": "deterministic_step_verification_v1",
        "step_id": step.get("id"),
        "platform_target": platform_target,
        "status": status,
        "checks": {
            "contradictory_evidence": contradictory,
            "worsening_signals": worsening,
            "failure_signals_present": has_failure_signals,
            "mutation_candidate": requires_strict,
        },
    }



