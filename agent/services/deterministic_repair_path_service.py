from __future__ import annotations

import copy
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

INITIAL_SIGNATURE_CATALOG_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "sig-ubuntu-apt-lock-conflict",
        "problem_class": "package_install_failure",
        "evidence_patterns": [
            r"could not get lock /var/lib/dpkg/lock",
            r"dpkg was interrupted",
            r"depends: .* but it is not going to be installed",
        ],
        "structured_fields": ["exit_code", "command"],
        "environment_constraints": {"platform_target": "ubuntu"},
        "confidence_weight": 1.3,
    },
    {
        "id": "sig-windows-service-timeout-1053",
        "problem_class": "service_start_failure",
        "evidence_patterns": [
            r"error 1053",
            r"service did not respond to the start or control request",
            r"timed out waiting for service",
        ],
        "structured_fields": ["service_name", "exit_code"],
        "environment_constraints": {"platform_target": "windows11"},
        "confidence_weight": 1.25,
    },
    {
        "id": "sig-service-restart-loop",
        "problem_class": "service_start_failure",
        "evidence_patterns": [
            r"restart loop",
            r"failed to start",
            r"start request repeated too quickly",
        ],
        "structured_fields": ["service_name", "status"],
        "confidence_weight": 1.35,
    },
    {
        "id": "sig-port-bind-conflict",
        "problem_class": "port_conflict",
        "evidence_patterns": [
            r"address already in use",
            r"eaddrinuse",
            r"bind.*failed",
        ],
        "structured_fields": ["port", "process_name"],
        "confidence_weight": 1.2,
    },
    {
        "id": "sig-command-not-found-path",
        "problem_class": "path_issue",
        "evidence_patterns": [
            r"command not found",
            r"is not recognized as an internal or external command",
            r"no such file or directory",
        ],
        "structured_fields": ["command", "path"],
        "confidence_weight": 1.15,
    },
    {
        "id": "sig-permission-denied",
        "problem_class": "permission_issue",
        "evidence_patterns": [
            r"permission denied",
            r"access is denied",
            r"operation not permitted",
        ],
        "structured_fields": ["path", "user"],
        "confidence_weight": 1.2,
    },
    {
        "id": "sig-compose-health-failure",
        "problem_class": "compose_failure",
        "evidence_patterns": [
            r"compose up failed",
            r"container .* is unhealthy",
            r"health check failed",
        ],
        "structured_fields": ["service_name", "container_id"],
        "confidence_weight": 1.2,
    },
    {
        "id": "sig-runtime-version-mismatch",
        "problem_class": "runtime_health_failure",
        "evidence_patterns": [
            r"unsupported runtime version",
            r"module not found",
            r"version mismatch",
        ],
        "structured_fields": ["runtime", "required_version", "actual_version"],
        "confidence_weight": 1.1,
    },
)

DIAGNOSIS_PROCEDURE_MODEL: dict[str, Any] = {
    "schema": "deterministic_diagnosis_procedure_v1",
    "supports_branching": True,
    "supports_stop_conditions": True,
    "step_types": ["collect_evidence", "evaluate_signature_outcome", "branch", "classify_case", "stop"],
    "required_step_fields": ["id", "step_type", "title", "mutation_candidate"],
    "non_destructive_policy": "enforced",
    "verification_checkpoints": ["evidence_collection_complete", "classification_emitted"],
}

INITIAL_DIAGNOSIS_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "service_start_failure": {
        "id": "diag-service-start-failure-v1",
        "problem_class": "service_start_failure",
        "entry_step_id": "collect-core-signals",
        "safe_stop_conditions": ["single_high_confidence", "sufficient_corroboration"],
        "steps": [
            {
                "id": "collect-core-signals",
                "step_type": "collect_evidence",
                "title": "Collect service status and core logs",
                "evidence_sources": ["service_status", "error_logs"],
                "next_step": "evaluate-signature-outcome",
                "mutation_candidate": False,
            },
            {
                "id": "evaluate-signature-outcome",
                "step_type": "evaluate_signature_outcome",
                "title": "Evaluate signature confidence",
                "next_by_outcome": {
                    "single_high_confidence": "emit-service-classification",
                    "ambiguous_high_confidence": "collect-runtime-corroboration",
                    "low_confidence": "collect-runtime-corroboration",
                    "no_match": "fallback-unknown-classification",
                },
                "stop_when": ["single_high_confidence"],
                "mutation_candidate": False,
            },
            {
                "id": "collect-runtime-corroboration",
                "step_type": "collect_evidence",
                "title": "Collect runtime corroboration evidence",
                "evidence_sources": ["runtime_state", "container_state"],
                "next_step": "branch-after-corroboration",
                "mutation_candidate": False,
            },
            {
                "id": "branch-after-corroboration",
                "step_type": "branch",
                "title": "Branch by confidence outcome after corroboration",
                "branches": {
                    "single_high_confidence": "emit-service-classification",
                    "ambiguous_high_confidence": "emit-review-required-classification",
                    "low_confidence": "fallback-unknown-classification",
                    "no_match": "fallback-unknown-classification",
                },
                "fallback_step": "fallback-unknown-classification",
                "mutation_candidate": False,
            },
            {
                "id": "emit-service-classification",
                "step_type": "classify_case",
                "title": "Emit deterministic service-start classification",
                "classification": "service_start_failure",
                "classification_confidence": "high",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "emit-review-required-classification",
                "step_type": "classify_case",
                "title": "Emit review-required service-start classification",
                "classification": "service_start_failure_review_required",
                "classification_confidence": "medium",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "fallback-unknown-classification",
                "step_type": "classify_case",
                "title": "Fallback to unknown/mixed classification",
                "classification": "unknown_or_mixed_failure",
                "classification_confidence": "low",
                "stop": True,
                "mutation_candidate": False,
            },
        ],
    },
    "package_install_failure": {
        "id": "diag-package-install-failure-v1",
        "problem_class": "package_install_failure",
        "entry_step_id": "collect-package-signals",
        "safe_stop_conditions": ["single_high_confidence"],
        "steps": [
            {
                "id": "collect-package-signals",
                "step_type": "collect_evidence",
                "title": "Collect package manager and dependency signals",
                "evidence_sources": ["error_logs", "package_state", "runtime_state"],
                "next_step": "evaluate-package-signatures",
                "mutation_candidate": False,
            },
            {
                "id": "evaluate-package-signatures",
                "step_type": "evaluate_signature_outcome",
                "title": "Evaluate package failure signatures",
                "next_by_outcome": {
                    "single_high_confidence": "emit-package-classification",
                    "ambiguous_high_confidence": "emit-package-review-classification",
                    "low_confidence": "fallback-package-unknown",
                    "no_match": "fallback-package-unknown",
                },
                "mutation_candidate": False,
            },
            {
                "id": "emit-package-classification",
                "step_type": "classify_case",
                "title": "Emit deterministic package classification",
                "classification": "package_install_failure",
                "classification_confidence": "high",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "emit-package-review-classification",
                "step_type": "classify_case",
                "title": "Emit package review-required classification",
                "classification": "package_install_failure_review_required",
                "classification_confidence": "medium",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "fallback-package-unknown",
                "step_type": "classify_case",
                "title": "Fallback to unknown package failure classification",
                "classification": "unknown_or_mixed_failure",
                "classification_confidence": "low",
                "stop": True,
                "mutation_candidate": False,
            },
        ],
    },
    "port_conflict": {
        "id": "diag-port-conflict-v1",
        "problem_class": "port_conflict",
        "entry_step_id": "collect-port-signals",
        "safe_stop_conditions": ["single_high_confidence", "ambiguous_high_confidence"],
        "steps": [
            {
                "id": "collect-port-signals",
                "step_type": "collect_evidence",
                "title": "Collect port and process state signals",
                "evidence_sources": ["port_state", "service_status", "error_logs"],
                "next_step": "evaluate-port-signatures",
                "mutation_candidate": False,
            },
            {
                "id": "evaluate-port-signatures",
                "step_type": "evaluate_signature_outcome",
                "title": "Evaluate port conflict signatures",
                "next_by_outcome": {
                    "single_high_confidence": "emit-port-classification",
                    "ambiguous_high_confidence": "emit-port-review-classification",
                    "low_confidence": "fallback-port-unknown",
                    "no_match": "fallback-port-unknown",
                },
                "mutation_candidate": False,
            },
            {
                "id": "emit-port-classification",
                "step_type": "classify_case",
                "title": "Emit deterministic port conflict classification",
                "classification": "port_conflict",
                "classification_confidence": "high",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "emit-port-review-classification",
                "step_type": "classify_case",
                "title": "Emit review-required port conflict classification",
                "classification": "port_conflict_review_required",
                "classification_confidence": "medium",
                "stop": True,
                "mutation_candidate": False,
            },
            {
                "id": "fallback-port-unknown",
                "step_type": "classify_case",
                "title": "Fallback to unknown port conflict classification",
                "classification": "unknown_or_mixed_failure",
                "classification_confidence": "low",
                "stop": True,
                "mutation_candidate": False,
            },
        ],
    },
}

REPAIR_PROCEDURE_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_procedure_v1",
    "required_fields": [
        "id",
        "problem_class",
        "safety_class",
        "preconditions",
        "steps",
        "postconditions",
        "verification",
        "rollback_hints",
    ],
    "safety_classes": ["safe", "review_first", "high_risk"],
    "execution_policy": "bounded_stepwise_with_verification",
}


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


def build_initial_failure_signature_catalog() -> tuple[FailureSignature, ...]:
    catalog: list[FailureSignature] = []
    for payload in INITIAL_SIGNATURE_CATALOG_DEFINITIONS:
        catalog.append(build_failure_signature(payload))
    return tuple(catalog)


def _extract_evidence_text(normalized_evidence: dict[str, Any]) -> str:
    chunks: list[str] = []
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        chunks.extend(
            [
                str(entry.get("summary") or ""),
                str(raw.get("message") or ""),
                str(raw.get("stderr") or ""),
                str(raw.get("stdout") or ""),
                str(raw.get("command") or ""),
                str(raw.get("health_check") or ""),
            ]
        )
    return "\n".join(piece for piece in chunks if piece).lower()


def _evaluate_environment_match(signature: FailureSignature, environment_facts: dict[str, Any]) -> dict[str, Any]:
    constraints = dict(signature.environment_constraints or {})
    if not constraints:
        return {
            "score": 1.0,
            "matched_constraints": [],
            "missing_constraints": [],
        }
    matched: list[str] = []
    missing: list[str] = []
    for key, expected in constraints.items():
        actual = str(environment_facts.get(key) or "").strip().lower()
        if actual == str(expected).strip().lower():
            matched.append(key)
        else:
            missing.append(key)
    score = len(matched) / len(constraints)
    return {
        "score": round(score, 3),
        "matched_constraints": matched,
        "missing_constraints": missing,
    }


def _evaluate_structured_field_match(signature: FailureSignature, normalized_evidence: dict[str, Any]) -> dict[str, Any]:
    fields = list(signature.structured_fields or [])
    if not fields:
        return {"score": 1.0, "matched_fields": []}
    available_fields: set[str] = set()
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        available_fields.update(str(key) for key in raw.keys())
    matched_fields = [field for field in fields if field in available_fields]
    score = len(matched_fields) / len(fields)
    return {"score": round(score, 3), "matched_fields": matched_fields}


def match_failure_signatures(
    *,
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    signature_catalog: tuple[FailureSignature, ...] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    catalog = signature_catalog or build_initial_failure_signature_catalog()
    evidence_text = _extract_evidence_text(normalized_evidence)
    matches: list[dict[str, Any]] = []
    for signature in catalog:
        compiled = signature.compiled_patterns()
        matched_patterns = [pattern.pattern for pattern in compiled if pattern.search(evidence_text)]
        if not matched_patterns:
            continue
        unmatched_patterns = [pattern.pattern for pattern in compiled if pattern.pattern not in matched_patterns]
        pattern_strength = len(matched_patterns) / len(compiled)
        environment_match = _evaluate_environment_match(signature, environment_facts)
        structured_match = _evaluate_structured_field_match(signature, normalized_evidence)
        weighted_score = (
            (pattern_strength * 0.7)
            + (environment_match["score"] * 0.2)
            + (structured_match["score"] * 0.1)
        ) * signature.confidence_weight
        score = round(min(1.0, weighted_score), 3)
        matches.append(
            {
                "signature_id": signature.id,
                "problem_class": signature.problem_class,
                "score": score,
                "confidence_weight": signature.confidence_weight,
                "signature_strength": round(pattern_strength, 3),
                "matched_patterns": matched_patterns,
                "unmatched_patterns": unmatched_patterns,
                "environment_match": environment_match["score"],
                "matched_environment_constraints": environment_match["matched_constraints"],
                "missing_environment_constraints": environment_match["missing_constraints"],
                "structured_field_match": structured_match["score"],
                "matched_structured_fields": structured_match["matched_fields"],
            }
        )
    ranked = sorted(matches, key=lambda item: (-float(item["score"]), item["signature_id"]))[: max(1, int(top_k))]
    return {
        "schema": "deterministic_signature_matching_v1",
        "match_count": len(ranked),
        "matches": ranked,
        "llm_used": False,
    }


def classify_signature_matching_outcome(
    *,
    ranked_matches: list[dict[str, Any]],
    confidence_model: dict[str, Any],
    ambiguity_delta: float = 0.08,
) -> dict[str, Any]:
    high_threshold = float(confidence_model.get("thresholds", {}).get("deterministic_execute", 0.78))
    review_threshold = float(confidence_model.get("thresholds", {}).get("review_required", 0.55))
    if not ranked_matches:
        return {
            "outcome": "no_match",
            "decision": "llm_escalation",
            "best_problem_class": None,
            "best_score": 0.0,
            "requires_review": True,
            "requires_llm_escalation": True,
            "recommended_next_steps": [
                "collect_additional_bounded_evidence",
                "run_fallback_diagnosis_playbook",
                "escalate_to_llm_with_bounded_context",
            ],
        }
    best = ranked_matches[0]
    best_score = float(best.get("score") or 0.0)
    second_score = float(ranked_matches[1].get("score") or 0.0) if len(ranked_matches) > 1 else 0.0
    is_ambiguous = len(ranked_matches) > 1 and abs(best_score - second_score) <= float(ambiguity_delta)
    if best_score < review_threshold:
        return {
            "outcome": "low_confidence",
            "decision": "deterministic_fallback_then_llm_if_needed",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": True,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "collect_corroborating_signals",
                "run_non_destructive_diagnosis_playbook",
                "avoid_mutation_until_confidence_improves",
            ],
        }
    if is_ambiguous:
        return {
            "outcome": "ambiguous_high_confidence",
            "decision": "review_required_before_mutation",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": True,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "run_branching_diagnosis_playbook",
                "collect_targeted_disambiguation_evidence",
                "present_top_signatures_for_operator_review",
            ],
        }
    if best_score >= high_threshold:
        return {
            "outcome": "single_high_confidence",
            "decision": "deterministic_repair_candidate",
            "best_problem_class": best.get("problem_class"),
            "best_score": best_score,
            "requires_review": False,
            "requires_llm_escalation": False,
            "recommended_next_steps": [
                "execute_deterministic_diagnosis_playbook",
                "prepare_bounded_repair_procedure_preview",
                "verify_before_and_after_each_mutation",
            ],
        }
    return {
        "outcome": "low_confidence",
        "decision": "review_required",
        "best_problem_class": best.get("problem_class"),
        "best_score": best_score,
        "requires_review": True,
        "requires_llm_escalation": False,
        "recommended_next_steps": [
            "collect_corroborating_signals",
            "run_non_destructive_diagnosis_playbook",
            "avoid_mutation_until_confidence_improves",
        ],
    }


def build_signature_explanation(
    *,
    match: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    matched_patterns = list(match.get("matched_patterns") or [])
    evidence_snippets: list[str] = []
    for entry in list(normalized_evidence.get("evidence") or []):
        raw = dict(entry.get("raw") or {})
        message = str(raw.get("message") or entry.get("summary") or "").strip()
        lowered = message.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in matched_patterns):
            evidence_snippets.append(message[:180])
        if len(evidence_snippets) >= 3:
            break
    platform_target = str(environment_facts.get("platform_target") or "unknown")
    return {
        "signature_id": match.get("signature_id"),
        "problem_class": match.get("problem_class"),
        "score": match.get("score"),
        "matched_patterns": matched_patterns,
        "matched_environment_constraints": list(match.get("matched_environment_constraints") or []),
        "key_evidence": evidence_snippets,
        "summary": (
            f"Matched {match.get('signature_id')} ({match.get('problem_class')}) with score {match.get('score')} "
            f"on platform {platform_target} using patterns: {', '.join(matched_patterns[:2])}."
        ),
    }


def get_initial_diagnosis_playbooks() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(INITIAL_DIAGNOSIS_PLAYBOOKS)


def validate_non_destructive_diagnosis_playbook(playbook: dict[str, Any]) -> None:
    mutating_step_types = {"execute_mutation", "apply_fix", "restart_service", "run_repair"}
    for step in list(playbook.get("steps") or []):
        step_type = str(step.get("step_type") or "").strip()
        if bool(step.get("mutation_candidate")):
            raise ValueError("diagnosis_playbook_contains_mutation_candidate")
        if step_type in mutating_step_types:
            raise ValueError("diagnosis_playbook_contains_mutating_step_type")


def run_diagnosis_playbook(
    *,
    playbook: dict[str, Any],
    normalized_evidence: dict[str, Any],
    matching_outcome: dict[str, Any],
    max_steps: int = 20,
) -> dict[str, Any]:
    validate_non_destructive_diagnosis_playbook(playbook)
    steps = list(playbook.get("steps") or [])
    step_map = {str(step.get("id") or ""): step for step in steps}
    if not steps:
        return {
            "schema": "deterministic_diagnosis_run_v1",
            "playbook_id": playbook.get("id"),
            "executed_steps": [],
            "state_updates": [],
            "final_state": "failed",
            "classification": None,
            "non_destructive_enforced": True,
        }

    current_step_id = str(playbook.get("entry_step_id") or steps[0]["id"])
    visited: set[str] = set()
    executed_steps: list[dict[str, Any]] = []
    state_updates: list[dict[str, Any]] = []
    classification = None
    final_state = "running"
    stopped_early = False

    for _ in range(max_steps):
        if not current_step_id:
            final_state = "completed"
            break
        if current_step_id in visited:
            final_state = "failed_loop_detected"
            state_updates.append({"event": "loop_detected", "step_id": current_step_id})
            break
        visited.add(current_step_id)
        step = step_map.get(current_step_id)
        if not step:
            final_state = "failed_missing_step"
            state_updates.append({"event": "missing_step", "step_id": current_step_id})
            break

        step_type = str(step.get("step_type") or "")
        executed_steps.append(
            {
                "step_id": current_step_id,
                "step_type": step_type,
                "title": step.get("title"),
            }
        )

        if step_type == "collect_evidence":
            expected_sources = list(step.get("evidence_sources") or [])
            available_sources = {
                str((dict(item.get("raw") or {})).get("source") or item.get("source") or "")
                for item in list(normalized_evidence.get("evidence") or [])
            }
            collected_sources = [source for source in expected_sources if source in available_sources]
            state_updates.append(
                {
                    "event": "evidence_collected",
                    "step_id": current_step_id,
                    "expected_sources": expected_sources,
                    "collected_sources": collected_sources,
                    "missing_sources": [source for source in expected_sources if source not in collected_sources],
                }
            )
            current_step_id = str(step.get("next_step") or "")
            continue

        if step_type == "evaluate_signature_outcome":
            outcome = str(matching_outcome.get("outcome") or "no_match")
            next_by_outcome = dict(step.get("next_by_outcome") or {})
            current_step_id = str(next_by_outcome.get(outcome) or step.get("next_step") or "")
            stop_when = set(step.get("stop_when") or [])
            state_updates.append(
                {
                    "event": "signature_outcome_evaluated",
                    "step_id": current_step_id or step.get("id"),
                    "outcome": outcome,
                    "stop_condition_met": outcome in stop_when,
                }
            )
            if outcome in stop_when and current_step_id:
                stopped_early = True
            continue

        if step_type == "branch":
            outcome = str(matching_outcome.get("outcome") or "no_match")
            branches = dict(step.get("branches") or {})
            fallback_step = str(step.get("fallback_step") or "")
            selected_step = str(branches.get(outcome) or fallback_step)
            state_updates.append(
                {
                    "event": "branch_selected",
                    "step_id": step.get("id"),
                    "outcome": outcome,
                    "selected_step": selected_step,
                    "fallback_used": selected_step == fallback_step,
                }
            )
            current_step_id = selected_step
            continue

        if step_type == "classify_case":
            classification = str(step.get("classification") or "unknown_or_mixed_failure")
            state_updates.append(
                {
                    "event": "classification_emitted",
                    "step_id": step.get("id"),
                    "classification": classification,
                    "confidence_band": step.get("classification_confidence"),
                }
            )
            if bool(step.get("stop")):
                final_state = "classified"
                break
            current_step_id = str(step.get("next_step") or "")
            continue

        if step_type == "stop":
            final_state = "stopped"
            state_updates.append({"event": "stop", "step_id": step.get("id"), "reason": step.get("reason")})
            break

        raise ValueError(f"diagnosis_playbook_unknown_step_type:{step_type}")

    else:
        final_state = "max_steps_reached"

    if final_state == "running":
        final_state = "completed"
    return {
        "schema": "deterministic_diagnosis_run_v1",
        "playbook_id": playbook.get("id"),
        "executed_steps": executed_steps,
        "state_updates": state_updates,
        "final_state": final_state,
        "classification": classification,
        "matching_outcome": matching_outcome.get("outcome"),
        "non_destructive_enforced": True,
        "stopped_early": stopped_early,
    }


def build_repair_procedure_template(*, problem_class: str, platform_target: str) -> dict[str, Any]:
    safety_class = "safe"
    if problem_class in {"permission_issue", "runtime_health_failure"}:
        safety_class = "high_risk"
    elif problem_class in {"package_install_failure", "compose_failure"}:
        safety_class = "review_first"
    return {
        "id": f"repair-procedure-{problem_class}-v1",
        "problem_class": problem_class,
        "platform_target": platform_target,
        "safety_class": safety_class,
        "preconditions": [
            "deterministic_diagnosis_completed",
            "non_destructive_checks_executed",
            "bounded_execution_scope_confirmed",
        ],
        "steps": [
            {
                "id": "repair-step-01",
                "title": "Prepare bounded repair preview",
                "mutation_candidate": False,
                "dry_run_supported": True,
            },
            {
                "id": "repair-step-02",
                "title": "Execute selected bounded repair action",
                "mutation_candidate": True,
                "dry_run_supported": True,
            },
            {
                "id": "repair-step-03",
                "title": "Run post-repair verification checks",
                "mutation_candidate": False,
                "dry_run_supported": False,
            },
        ],
        "postconditions": [
            "repair_outcome_recorded",
            "verification_result_classified",
        ],
        "verification": {
            "required": True,
            "checks": ["service_health", "command_exit_code", "target_specific_health_probe"],
        },
        "rollback_hints": [
            "prefer_reversible_actions_when_available",
            "record_manual_recovery_hint_for_non_reversible_actions",
        ],
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

    normalized_evidence = normalize_evidence_bundle(
        evidence_items=evidence_items,
        environment_facts=environment_facts,
    )
    confidence = evaluate_repair_confidence(
        signature_strength=0.7,
        platform_match=1.0 if environment_facts.get("platform_target") in {"windows11", "ubuntu"} else 0.35,
        history_success_rate=0.5,
    )
    signature_catalog = build_initial_failure_signature_catalog()
    signature_matching = match_failure_signatures(
        normalized_evidence=normalized_evidence,
        environment_facts=environment_facts,
        signature_catalog=signature_catalog,
    )
    matching_outcome = classify_signature_matching_outcome(
        ranked_matches=list(signature_matching.get("matches") or []),
        confidence_model=confidence,
    )
    signature_explanations = [
        build_signature_explanation(
            match=match,
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
        )
        for match in list(signature_matching.get("matches") or [])[:3]
    ]
    diagnosis_playbooks = get_initial_diagnosis_playbooks()
    selected_problem_class = str(
        matching_outcome.get("best_problem_class")
        or "service_start_failure"
    )
    selected_playbook = diagnosis_playbooks.get(selected_problem_class) or diagnosis_playbooks["service_start_failure"]
    diagnosis_run = run_diagnosis_playbook(
        playbook=selected_playbook,
        normalized_evidence=normalized_evidence,
        matching_outcome=matching_outcome,
    )
    repair_procedure_template = build_repair_procedure_template(
        problem_class=selected_problem_class,
        platform_target=str(environment_facts.get("platform_target") or "unknown"),
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
        "normalized_evidence": normalized_evidence,
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
        "initial_signature_catalog": {
            "schema": "deterministic_failure_signature_catalog_v1",
            "entries": [signature_to_dict(signature) for signature in signature_catalog],
        },
        "signature_matching": signature_matching,
        "signature_matching_outcome": matching_outcome,
        "signature_explanations": signature_explanations,
        "diagnosis_procedure_model": dict(DIAGNOSIS_PROCEDURE_MODEL),
        "diagnosis_playbooks": {
            "schema": "deterministic_diagnosis_playbook_catalog_v1",
            "entries": list(diagnosis_playbooks.values()),
        },
        "diagnosis_run": diagnosis_run,
        "non_destructive_diagnosis_policy": {
            "enforced": True,
            "rule": "no_mutation_candidate_steps_in_diagnosis_playbooks",
        },
        "repair_procedure_model": dict(REPAIR_PROCEDURE_MODEL),
        "repair_procedure_template": repair_procedure_template,
    }
