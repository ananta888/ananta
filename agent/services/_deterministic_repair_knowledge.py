"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Golden-path examples, rollout plan, test coverage manifest.

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




def build_golden_path_examples() -> dict[str, Any]:
    return {
        "schema": "deterministic_repair_golden_paths_v1",
        "examples": [
            {
                "id": "golden-service-start-failure",
                "problem_class": "service_start_failure",
                "flow": ["diagnosis", "proposal_preview", "verification", "result_recording"],
            },
            {
                "id": "golden-package-install-failure",
                "problem_class": "package_install_failure",
                "flow": ["diagnosis", "proposal_preview", "verification", "result_recording"],
            },
            {
                "id": "golden-port-conflict",
                "problem_class": "port_conflict",
                "flow": ["diagnosis", "proposal_preview", "verification", "result_recording"],
            },
        ],
    }





def build_rollout_plan() -> dict[str, Any]:
    return {
        "schema": "deterministic_repair_rollout_plan_v1",
        "phases": [
            {
                "name": "pilot",
                "supported_classes": ["service_start_failure", "package_install_failure"],
                "gating": "approval_required_for_mutation",
            },
            {
                "name": "expanded_common_classes",
                "supported_classes": ["port_conflict", "path_issue", "compose_failure"],
                "gating": "bounded_execution_and_guardrails",
            },
            {
                "name": "governed_default",
                "supported_classes": ["all_curated_classes"],
                "gating": "audit_and_policy_enforced",
            },
        ],
        "rollout_mode": "phased",
    }





def build_test_coverage_manifest() -> dict[str, Any]:
    return {
        "schema": "deterministic_repair_test_coverage_manifest_v1",
        "coverage_areas": [
            {
                "area": "signature_matching",
                "status": "covered",
                "focus": ["representative_failure_classes", "ambiguous_and_no_match_paths"],
            },
            {
                "area": "diagnosis_and_repair_flows",
                "status": "covered",
                "focus": ["approval_gating", "verification_and_safe_stop"],
            },
            {
                "area": "memory_and_ranking",
                "status": "covered",
                "focus": ["success_failure_recording", "environment_similarity", "negative_learning"],
            },
        ],
    }



