from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Pattern


REPAIR_PATH_TARGET_MODEL: dict[str, Any] = {
    "model_id": "deterministic_repair_path_v1",
    "strategy": "deterministic_first_llm_escalation",
    "phases": [
        "detected",
        "diagnosing",
        "proposing",
        "approval_required",
        "executing",
        "verifying",
        "succeeded_or_failed",
    ],
    "escalation_triggers": [
        "unknown_signature",
        "ambiguous_match",
        "low_confidence",
        "exhausted_deterministic_paths",
        "contradictory_evidence",
    ],
}

REPAIR_PROBLEM_CLASS_INVENTORY: dict[str, dict[str, Any]] = {
    "package_install_failure": {
        "signals": ["package manager error", "dependency resolution failed", "package lock conflict"],
        "safe_action_range": ["inspect_only", "bounded_package_repair"],
        "examples": ["apt dependency conflict", "winget package install failure"],
    },
    "service_start_failure": {
        "signals": ["service failed to start", "restart loop", "startup timeout"],
        "safe_action_range": ["inspect_only", "bounded_service_restart"],
        "examples": ["systemd unit failed", "windows service crash loop"],
    },
    "port_conflict": {
        "signals": ["address already in use", "bind failed", "port unavailable"],
        "safe_action_range": ["inspect_only", "bounded_port_conflict_resolution"],
        "examples": ["api cannot bind 5000", "compose service port collision"],
    },
    "path_issue": {
        "signals": ["command not found", "binary not in path", "path resolution failed"],
        "safe_action_range": ["inspect_only", "bounded_path_repair"],
        "examples": ["python not found", "docker cli missing from path"],
    },
    "permission_issue": {
        "signals": ["permission denied", "access forbidden", "ownership mismatch"],
        "safe_action_range": ["inspect_only", "review_first_permission_fix"],
        "examples": ["cannot write config file", "service user lacks access"],
    },
    "compose_failure": {
        "signals": ["compose up failed", "service unhealthy", "container exited"],
        "safe_action_range": ["inspect_only", "bounded_compose_repair"],
        "examples": ["compose dependency unavailable", "container healthcheck failing"],
    },
    "runtime_health_failure": {
        "signals": ["runtime unhealthy", "runtime dependency missing", "runtime check failed"],
        "safe_action_range": ["inspect_only", "bounded_runtime_repair"],
        "examples": ["node runtime mismatch", "python environment broken"],
    },
}

REPAIR_STATE_MODEL: dict[str, Any] = {
    "states": [
        "detected",
        "diagnosing",
        "proposing",
        "approval_required",
        "executing",
        "verifying",
        "succeeded",
        "failed",
        "escalated",
    ],
    "transitions": {
        "detected": ["diagnosing"],
        "diagnosing": ["proposing", "escalated", "failed"],
        "proposing": ["approval_required", "executing", "escalated", "failed"],
        "approval_required": ["executing", "failed"],
        "executing": ["verifying", "failed"],
        "verifying": ["succeeded", "failed", "escalated"],
        "succeeded": [],
        "failed": [],
        "escalated": [],
    },
    "terminal_states": ["succeeded", "failed", "escalated"],
}

CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "deterministic_execute": 0.78,
    "review_required": 0.55,
}

ALLOWED_EVIDENCE_TYPES: tuple[str, ...] = (
    "log_entry",
    "command_result",
    "health_check",
    "service_status",
    "environment_fact",
    "config_snippet",
)

SEVERITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("critical", r"\b(critical|panic|fatal)\b"),
    ("error", r"\b(error|failed|exception|denied)\b"),
    ("warning", r"\b(warn|warning|degraded)\b"),
    ("info", r"\b(info|started|healthy|ok)\b"),
)


@dataclass(frozen=True)
class FailureSignature:
    id: str
    problem_class: str
    evidence_patterns: tuple[str, ...]
    structured_fields: tuple[str, ...] = ()
    environment_constraints: dict[str, str] = field(default_factory=dict)
    confidence_weight: float = 1.0

    def compiled_patterns(self) -> tuple[Pattern[str], ...]:
        compiled: list[Pattern[str]] = []
        for pattern in self.evidence_patterns:
            compiled.append(re.compile(pattern, flags=re.IGNORECASE))
        return tuple(compiled)


def build_failure_signature(payload: dict[str, Any]) -> FailureSignature:
    raw_patterns = payload.get("evidence_patterns") or []
    patterns = tuple(str(item).strip() for item in raw_patterns if str(item).strip())
    if not patterns:
        raise ValueError("failure_signature_requires_patterns")
    problem_class = str(payload.get("problem_class") or "").strip()
    if problem_class not in REPAIR_PROBLEM_CLASS_INVENTORY:
        raise ValueError("failure_signature_problem_class_unknown")
    confidence_weight = float(payload.get("confidence_weight", 1.0))
    confidence_weight = min(2.0, max(0.1, confidence_weight))
    return FailureSignature(
        id=str(payload.get("id") or "").strip() or "signature-unnamed",
        problem_class=problem_class,
        evidence_patterns=patterns,
        structured_fields=tuple(str(item).strip() for item in payload.get("structured_fields", []) if str(item).strip()),
        environment_constraints={str(k): str(v) for k, v in dict(payload.get("environment_constraints") or {}).items()},
        confidence_weight=confidence_weight,
    )


def signature_to_dict(signature: FailureSignature) -> dict[str, Any]:
    return {
        "id": signature.id,
        "problem_class": signature.problem_class,
        "evidence_patterns": list(signature.evidence_patterns),
        "structured_fields": list(signature.structured_fields),
        "environment_constraints": dict(signature.environment_constraints),
        "confidence_weight": signature.confidence_weight,
    }


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


def _detect_severity(message: str) -> str:
    text = str(message or "").lower()
    for severity, pattern in SEVERITY_PATTERNS:
        if re.search(pattern, text):
            return severity
    return "info"


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


def build_deterministic_repair_foundation_snapshot(
    *,
    mode_data: dict[str, Any],
    issue_symptom: str,
    evidence_sources: list[str],
) -> dict[str, Any]:
    environment_facts = collect_environment_facts(mode_data)
    synthetic_logs = ingest_structured_logs(
        [
            {
                "message": issue_symptom,
                "severity": "error",
                "source": "issue_symptom",
            }
        ],
        source="issue_symptom",
    )
    evidence_items: list[dict[str, Any]] = []
    evidence_items.extend(synthetic_logs)
    evidence_items.append(
        {
            "type": "environment_fact",
            "source": "environment",
            "severity": "info",
            "message": f"platform={environment_facts.get('platform_target')}",
        }
    )
    for source in evidence_sources:
        evidence_items.append(
            {
                "type": "service_status" if source == "service_status" else "health_check",
                "source": source,
                "severity": "info",
                "message": f"source_enabled:{source}",
            }
        )

    confidence = evaluate_repair_confidence(
        signature_strength=0.7,
        platform_match=1.0 if environment_facts.get("platform_target") in {"windows11", "ubuntu"} else 0.35,
        history_success_rate=0.5,
    )
    return {
        "target_model": dict(REPAIR_PATH_TARGET_MODEL),
        "problem_class_inventory": dict(REPAIR_PROBLEM_CLASS_INVENTORY),
        "state_model": dict(REPAIR_STATE_MODEL),
        "confidence_model": confidence,
        "evidence_ingestion_model": {
            "allowed_evidence_types": list(ALLOWED_EVIDENCE_TYPES),
            "selected_sources": list(evidence_sources),
            "bounded_collection": {
                "max_sources": 8,
                "max_log_entries_per_source": 200,
                "max_chars_per_entry": 4000,
            },
        },
        "environment_facts": environment_facts,
        "normalized_evidence": normalize_evidence_bundle(
            evidence_items=evidence_items,
            environment_facts=environment_facts,
        ),
        "failure_signature_model": {
            "schema": "failure_signature_v1",
            "fields": [
                "id",
                "problem_class",
                "evidence_patterns",
                "structured_fields",
                "environment_constraints",
                "confidence_weight",
            ],
        },
    }
