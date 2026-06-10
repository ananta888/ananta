"""Internal sub-module of deterministic_repair_path_service.

Extracted from the monolithic agent.services.deterministic_repair_path_service
to keep the main module small. This module owns: Environment similarity, environment-fact collection, structured-log ingest, command-result capture, evidence-bundle normalization.

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
    ALLOWED_EVIDENCE_TYPES,
    ENVIRONMENT_SIMILARITY_MODEL,
)
from agent.services import _deterministic_repair_utils as _drr_utils
_detect_severity = _drr_utils._detect_severity


log = logging.getLogger(__name__)




def compute_environment_similarity(
    *,
    current_environment_facts: dict[str, Any],
    reference_environment_facts: dict[str, Any],
) -> dict[str, Any]:
    weights = dict(ENVIRONMENT_SIMILARITY_MODEL["weights"])
    matched_fields: list[str] = []
    comparisons: list[dict[str, Any]] = []
    score = 0.0
    for field, weight in weights.items():
        current_value = str(current_environment_facts.get(field) or "").strip().lower()
        reference_value = str(reference_environment_facts.get(field) or "").strip().lower()
        matched = bool(current_value and reference_value and current_value == reference_value)
        if matched:
            matched_fields.append(field)
            score += float(weight)
        comparisons.append(
            {
                "field": field,
                "weight": float(weight),
                "current_value": current_value or None,
                "reference_value": reference_value or None,
                "matched": matched,
            }
        )
    return {
        "schema": "deterministic_environment_similarity_result_v1",
        "score": round(min(1.0, score), 3),
        "matched_fields": matched_fields,
        "comparisons": comparisons,
    }





def collect_environment_facts(mode_data: dict[str, Any]) -> dict[str, Any]:
    platform = str(mode_data.get("platform_target") or "unknown").strip().lower() or "unknown"
    facts = {
        "os_family": "windows" if platform == "windows11" else ("linux" if platform == "ubuntu" else "unknown"),
        "platform_target": platform,
        "distro": str(mode_data.get("distro") or ("ubuntu" if platform == "ubuntu" else "windows11" if platform == "windows11" else "unknown")),
        "package_manager": str(mode_data.get("package_manager") or ("apt_dpkg" if platform == "ubuntu" else "winget_or_choco" if platform == "windows11" else "unknown")),
        "runtime_versions": dict(mode_data.get("runtime_versions") or {}),
        "container_state": str(mode_data.get("container_state") or "unknown"),
        "service_state": str(mode_data.get("service_state") or "unknown"),
    }
    return facts





def ingest_structured_logs(logs: list[dict[str, Any]] | list[str], *, source: str = "unknown") -> list[dict[str, Any]]:
    structured: list[dict[str, Any]] = []
    for index, entry in enumerate(logs or [], start=1):
        if isinstance(entry, dict):
            message = str(entry.get("message") or "").strip()
            timestamp = str(entry.get("timestamp") or "").strip() or None
            severity = str(entry.get("severity") or "").strip().lower() or _detect_severity(message)
            entry_source = str(entry.get("source") or source).strip() or source
        else:
            message = str(entry).strip()
            timestamp = None
            severity = _detect_severity(message)
            entry_source = source
        if not message:
            continue
        structured.append(
            {
                "type": "log_entry",
                "source": entry_source,
                "timestamp": timestamp,
                "severity": severity,
                "message": message,
                "provenance": {"ingested_from": source, "index": index},
            }
        )
    return structured





def capture_command_result(
    *,
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    health_check: str | None = None,
    source: str = "command_runner",
) -> dict[str, Any]:
    return {
        "type": "command_result",
        "source": source,
        "command": str(command).strip(),
        "exit_code": int(exit_code),
        "stdout": str(stdout or "").strip(),
        "stderr": str(stderr or "").strip(),
        "health_check": str(health_check or "").strip() or None,
        "status": "success" if int(exit_code) == 0 else "failure",
    }





def normalize_evidence_bundle(
    *,
    evidence_items: list[dict[str, Any]],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    dropped_noise = 0
    for item in evidence_items or []:
        item_type = str(item.get("type") or "").strip()
        if item_type not in ALLOWED_EVIDENCE_TYPES:
            continue
        summary_key = str(item.get("message") or item.get("command") or item.get("source") or "").strip().lower()
        dedupe_key = (item_type, summary_key)
        if dedupe_key in seen_keys:
            dropped_noise += 1
            continue
        seen_keys.add(dedupe_key)
        normalized.append(
            {
                "type": item_type,
                "source": str(item.get("source") or "unknown"),
                "severity": str(item.get("severity") or "info"),
                "summary": summary_key[:240],
                "raw": dict(item),
            }
        )
    return {
        "schema": "deterministic_repair_evidence_v1",
        "environment_facts": dict(environment_facts or {}),
        "evidence": normalized,
        "metrics": {
            "ingested_count": len(evidence_items or []),
            "normalized_count": len(normalized),
            "dropped_noise_count": dropped_noise,
        },
    }



