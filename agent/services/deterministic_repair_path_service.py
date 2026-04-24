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

REPAIR_EXECUTION_SAFETY_POLICY: dict[str, Any] = {
    "schema": "deterministic_repair_safety_policy_v1",
    "requires_approval_for_safety_classes": ["review_first", "high_risk"],
    "requires_approval_for_action_safety_classes": ["confirm_required", "high_risk"],
    "allow_unbounded_actions": False,
    "allow_unknown_actions": False,
}

REPAIR_VERIFICATION_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_verification_v1",
    "step_verification_required_for_mutations": True,
    "allowed_probes": ["health_check", "service_status", "command_result", "functional_probe"],
    "final_verification_required": True,
}

REPAIR_OUTCOME_MEMORY_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_outcome_memory_v1",
    "required_fields": [
        "signature_id",
        "problem_class",
        "environment_facts",
        "procedure_id",
        "execution_status",
        "outcome_label",
        "verification_evidence",
    ],
    "query_keys": ["problem_class", "platform_target", "outcome_label", "procedure_id", "signature_id"],
}

ENVIRONMENT_SIMILARITY_MODEL: dict[str, Any] = {
    "schema": "deterministic_environment_similarity_v1",
    "weights": {
        "platform_target": 0.3,
        "os_family": 0.2,
        "package_manager": 0.2,
        "service_state": 0.2,
        "container_state": 0.1,
    },
}

REPAIR_ACTION_SAFETY_CLASSES: dict[str, Any] = {
    "schema": "deterministic_repair_action_safety_classes_v1",
    "classes": ["inspect_only", "bounded_low_risk", "confirm_required", "high_risk"],
    "rules": {
        "inspect_only": "non_mutating observation or verification step",
        "bounded_low_risk": "bounded mutation with low blast radius",
        "confirm_required": "mutation requires explicit confirmation and scoped approval",
        "high_risk": "mutation has high impact or weak rollback guarantees",
    },
}

APPROVAL_REQUIREMENT_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_approval_requirement_v1",
    "scope_dimensions": ["procedure_id", "target_scope", "session_id"],
    "enforcement": "backend_enforced",
}

LLM_ESCALATION_POLICY_MODEL: dict[str, Any] = {
    "schema": "deterministic_llm_escalation_policy_v1",
    "allowed_reasons": [
        "unknown_signature",
        "ambiguous_high_confidence",
        "low_confidence",
        "contradictory_evidence",
        "exhausted_deterministic_paths",
    ],
    "forbidden_when": [
        "single_high_confidence_without_contradictions",
        "deterministic_path_succeeded",
    ],
}

UNSAFE_ACTION_GUARDRAIL_MODEL: dict[str, Any] = {
    "schema": "deterministic_unsafe_action_guardrails_v1",
    "blocked_patterns": [
        r"rm\s+-rf\s+/",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdel\s+/f\s+/s\b",
        r":\(\)\{:\|:&\};:",
    ],
    "out_of_scope_patterns": [
        r"\bkubernetes\b",
        r"\bterraform\b",
        r"\bnetwork\s+firewall\s+rewrite\b",
        r"\bactive\s+directory\s+domain\b",
    ],
    "fail_closed": True,
}

OPERATOR_VIEW_MODEL: dict[str, Any] = {
    "schema": "deterministic_operator_views_v1",
    "views": [
        "session_summary",
        "path_visibility",
        "proposal_preview",
        "history_inspection",
    ],
}

OPERATOR_GUIDE_METADATA: dict[str, Any] = {
    "schema": "deterministic_operator_guide_v1",
    "doc_path": "docs/deterministic-repair-operator-guide.md",
    "topics": [
        "deterministic_flow",
        "approval_and_guardrails",
        "llm_escalation",
        "safety_notes",
    ],
}

ROLLOUT_PLAN_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_rollout_plan_v1",
    "phases": ["pilot", "expanded_common_classes", "governed_default"],
}

TEST_COVERAGE_MODEL: dict[str, Any] = {
    "schema": "deterministic_repair_test_coverage_v1",
    "areas": [
        "signature_matching",
        "diagnosis_and_repair_execution",
        "memory_and_ranking",
        "governance_and_escalation",
    ],
}

STANDARD_OUTCOME_LABELS: tuple[str, ...] = ("succeeded", "partially_helped", "failed", "regressed")


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


def build_initial_repair_procedure_catalog(
    *, signature_catalog: tuple[FailureSignature, ...] | None = None
) -> dict[str, Any]:
    catalog = signature_catalog or build_initial_failure_signature_catalog()
    signature_ids_by_problem_class: dict[str, list[str]] = {}
    for signature in catalog:
        signature_ids_by_problem_class.setdefault(signature.problem_class, []).append(signature.id)

    entries: list[dict[str, Any]] = []
    for problem_class in REPAIR_PROBLEM_CLASS_INVENTORY.keys():
        template = build_repair_procedure_template(problem_class=problem_class, platform_target="cross_platform")
        entries.append(
            {
                "id": f"repair-catalog-{problem_class}-v1",
                "problem_class": problem_class,
                "trigger_signature_ids": signature_ids_by_problem_class.get(problem_class, []),
                "trigger_diagnosis_outcomes": [problem_class, f"{problem_class}_review_required"],
                "bounded_scope_only": True,
                "procedure": template,
            }
        )
    return {
        "schema": "deterministic_repair_catalog_v1",
        "entries": entries,
        "bounded_scope_only": True,
    }


def select_repair_procedure_from_catalog(
    *,
    repair_catalog: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    entries = list(repair_catalog.get("entries") or [])
    if not entries:
        return {}
    target_problem_class = str(matching_outcome.get("best_problem_class") or "service_start_failure")
    for entry in entries:
        if str(entry.get("problem_class") or "") == target_problem_class:
            return entry
    return entries[0]


def build_repair_procedure_preview(
    *,
    selected_catalog_entry: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    steps = list(procedure.get("steps") or [])
    return {
        "schema": "deterministic_repair_preview_v1",
        "procedure_id": procedure.get("id"),
        "problem_class": selected_catalog_entry.get("problem_class"),
        "step_count": len(steps),
        "dry_run_supported": any(bool(step.get("dry_run_supported")) for step in steps),
        "mutation_step_ids": [step.get("id") for step in steps if bool(step.get("mutation_candidate"))],
        "matching_outcome": matching_outcome.get("outcome"),
        "limitations": [
            "preview_only_does_not_apply_state_changes",
            "verification_results_in_preview_are_predictive_not_observed",
        ],
    }


def _detect_contradictory_evidence(normalized_evidence: dict[str, Any]) -> bool:
    text = _extract_evidence_text(normalized_evidence)
    has_healthy = bool(re.search(r"\b(healthy|running|ok|resolved)\b", text))
    has_failure = bool(re.search(r"\b(failed|error|denied|panic|unhealthy)\b", text))
    return has_healthy and has_failure


def _detect_worsening_signals(normalized_evidence: dict[str, Any]) -> bool:
    text = _extract_evidence_text(normalized_evidence)
    return bool(re.search(r"\b(regressed|worse|panic|fatal|crash loop)\b", text))


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


def execute_repair_procedure(
    *,
    selected_catalog_entry: dict[str, Any],
    normalized_evidence: dict[str, Any],
    environment_facts: dict[str, Any],
    dry_run: bool,
    approval_policy: dict[str, Any] | None = None,
    safety_policy: dict[str, Any] | None = None,
    session_id: str = "repair-session-default",
    target_scope: str = "service_runtime",
    stop_on_contradictory_evidence: bool = True,
    stop_on_worsening_signals: bool = True,
) -> dict[str, Any]:
    policy = dict(REPAIR_EXECUTION_SAFETY_POLICY)
    if safety_policy:
        policy.update(dict(safety_policy))
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    procedure_id = str(procedure.get("id") or "unknown_procedure")
    steps = list(procedure.get("steps") or [])
    guardrail_evaluation = evaluate_unsafe_action_guardrails(
        proposed_actions=[str(step.get("title") or "") for step in steps],
    )
    procedure_safety_class = str(procedure.get("safety_class") or "safe")
    procedure_requires_approval = procedure_safety_class in set(policy.get("requires_approval_for_safety_classes") or [])
    scope_key = _approval_scope_key(
        procedure_id=procedure_id,
        target_scope=str(target_scope),
        session_id=str(session_id),
    )
    approved_scopes = {str(item) for item in list((approval_policy or {}).get("approved_scopes") or []) if str(item)}
    approval_context = {
        "required": procedure_requires_approval,
        "approved_mutations": bool((approval_policy or {}).get("approved_mutations", False)),
        "scope_key": scope_key,
        "scope_dimensions": {
            "procedure_id": procedure_id,
            "target_scope": str(target_scope),
            "session_id": str(session_id),
        },
        "approved_scopes": sorted(approved_scopes),
    }
    contradictory = _detect_contradictory_evidence(normalized_evidence)
    worsening = _detect_worsening_signals(normalized_evidence)
    step_records: list[dict[str, Any]] = []
    abort_conditions: list[dict[str, Any]] = []
    stop_reason = None

    if guardrail_evaluation["blocked_actions"] and guardrail_evaluation["fail_closed"]:
        abort_conditions.append(
            {
                "code": "unsafe_action_guardrail_block",
                "severity": "critical",
                "step_id": None,
                "message": "Execution blocked by unsafe action guardrails.",
                "blocked_actions": list(guardrail_evaluation["blocked_actions"]),
            }
        )
        return {
            "schema": "deterministic_repair_execution_v1",
            "procedure_id": procedure_id,
            "problem_class": selected_catalog_entry.get("problem_class"),
            "procedure_safety_class": procedure_safety_class,
            "execution_mode": "dry_run" if dry_run else "apply",
            "status": "blocked",
            "safety_policy": policy,
            "approval": approval_context,
            "steps": step_records,
            "abort_conditions": abort_conditions,
            "stop_reason": "unsafe_action_guardrail_block",
            "clean_stop": True,
            "bounded_execution_enforced": True,
            "worsening_signals_detected": worsening,
            "contradictory_evidence_detected": contradictory,
            "unsafe_action_guardrails": guardrail_evaluation,
        }

    for step in steps:
        step_id = str(step.get("id") or "")
        mutation_candidate = bool(step.get("mutation_candidate"))
        action_safety_class = classify_repair_action_safety(
            step=step,
            procedure_safety_class=procedure_safety_class,
        )
        if action_safety_class not in set(REPAIR_ACTION_SAFETY_CLASSES["classes"]):
            abort_conditions.append(
                {
                    "code": "unknown_action_safety_class",
                    "severity": "critical",
                    "step_id": step_id,
                    "message": "Unknown action safety class rejected by bounded execution policy.",
                }
            )
            stop_reason = "unknown_action_safety_class"
            break
        action_requires_approval = action_safety_class in set(policy.get("requires_approval_for_action_safety_classes") or [])
        scope_approved = approval_context["approved_mutations"] or scope_key in approved_scopes
        verification = run_step_verification(
            step=step,
            normalized_evidence=normalized_evidence,
            environment_facts=environment_facts,
        )
        record = {
            "step_id": step_id,
            "title": step.get("title"),
            "mutation_candidate": mutation_candidate,
            "action_safety_class": action_safety_class,
            "requires_approval": action_requires_approval,
            "dry_run_supported": bool(step.get("dry_run_supported")),
            "verification": verification,
            "state": "previewed" if dry_run else "executed",
            "verifiable": True,
            "audit_hint": f"repair_execution:{procedure_id}:{step_id}",
        }
        if mutation_candidate and action_requires_approval and not scope_approved:
            record["state"] = "blocked"
            stop_reason = "approval_required"
            abort_conditions.append(
                {
                    "code": "approval_required_for_mutation",
                    "severity": "high",
                    "step_id": step_id,
                    "message": "Mutation step blocked because scoped approval is required and missing.",
                }
            )
            step_records.append(record)
            break
        if mutation_candidate and stop_on_worsening_signals and verification["checks"]["worsening_signals"]:
            record["state"] = "aborted"
            stop_reason = "worsening_signals"
            abort_conditions.append(
                {
                    "code": "abort_on_worsening_signals",
                    "severity": "critical",
                    "step_id": step_id,
                    "message": "Execution aborted due to worsening signals before mutation.",
                }
            )
            step_records.append(record)
            break
        if mutation_candidate and stop_on_contradictory_evidence and verification["checks"]["contradictory_evidence"]:
            record["state"] = "aborted"
            stop_reason = "contradictory_evidence"
            abort_conditions.append(
                {
                    "code": "abort_on_contradictory_evidence",
                    "severity": "high",
                    "step_id": step_id,
                    "message": "Execution aborted due to contradictory evidence that makes repair unsafe.",
                }
            )
            step_records.append(record)
            break
        step_records.append(record)

    if dry_run:
        status = "preview_only"
    elif abort_conditions:
        status = "aborted"
    else:
        status = "completed"
    return {
        "schema": "deterministic_repair_execution_v1",
        "procedure_id": procedure_id,
        "problem_class": selected_catalog_entry.get("problem_class"),
        "procedure_safety_class": procedure_safety_class,
        "execution_mode": "dry_run" if dry_run else "apply",
        "status": status,
        "safety_policy": policy,
        "approval": approval_context,
        "steps": step_records,
        "abort_conditions": abort_conditions,
        "stop_reason": stop_reason,
        "clean_stop": bool(abort_conditions),
        "bounded_execution_enforced": True,
        "worsening_signals_detected": worsening,
        "contradictory_evidence_detected": contradictory,
        "unsafe_action_guardrails": guardrail_evaluation,
    }


def verify_final_repair_outcome(
    *,
    execution_result: dict[str, Any],
    normalized_evidence: dict[str, Any],
    matching_outcome: dict[str, Any],
) -> dict[str, Any]:
    step_results = list(execution_result.get("steps") or [])
    statuses = [str((step.get("verification") or {}).get("status") or "needs_review") for step in step_results]
    has_fail = "fail" in statuses
    has_review = "needs_review" in statuses or "warning" in statuses
    contradictory = bool(execution_result.get("contradictory_evidence_detected"))
    worsening = bool(execution_result.get("worsening_signals_detected"))
    status = str(execution_result.get("status") or "aborted")
    if worsening:
        outcome_label = "regressed"
    elif status == "completed" and not has_fail and not contradictory and not has_review:
        outcome_label = "succeeded"
    elif status in {"completed", "preview_only"} and not has_fail:
        outcome_label = "partially_helped"
    else:
        outcome_label = "failed"
    return {
        "schema": "deterministic_repair_final_verification_v1",
        "outcome_label": outcome_label,
        "allowed_outcome_labels": list(STANDARD_OUTCOME_LABELS),
        "problem_class": execution_result.get("problem_class"),
        "matching_outcome": matching_outcome.get("outcome"),
        "evidence_based": True,
        "verification_summary": {
            "step_verification_statuses": statuses,
            "contradictory_evidence": contradictory,
            "worsening_signals": worsening,
            "execution_status": status,
        },
    }


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


def build_repair_outcome_memory_entry(
    *,
    signature_matching: dict[str, Any],
    selected_catalog_entry: dict[str, Any],
    environment_facts: dict[str, Any],
    execution_result: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    top_match = (list(signature_matching.get("matches") or [{}]) or [{}])[0]
    return {
        "schema": "deterministic_repair_outcome_memory_entry_v1",
        "signature_id": top_match.get("signature_id"),
        "problem_class": selected_catalog_entry.get("problem_class"),
        "environment_facts": {
            "platform_target": environment_facts.get("platform_target"),
            "os_family": environment_facts.get("os_family"),
            "package_manager": environment_facts.get("package_manager"),
            "service_state": environment_facts.get("service_state"),
        },
        "procedure_id": execution_result.get("procedure_id"),
        "execution_status": execution_result.get("status"),
        "outcome_label": final_verification.get("outcome_label"),
        "verification_evidence": final_verification.get("verification_summary"),
    }


def track_repair_outcomes(memory_entries: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {label: 0 for label in STANDARD_OUTCOME_LABELS}
    for entry in memory_entries:
        label = str(entry.get("outcome_label") or "failed")
        if label not in counts:
            counts[label] = 0
        counts[label] += 1
    total = sum(counts.values())
    recommendation_score = 0.0
    if total > 0:
        recommendation_score = (
            (counts.get("succeeded", 0) * 1.0)
            + (counts.get("partially_helped", 0) * 0.4)
            - (counts.get("failed", 0) * 0.5)
            - (counts.get("regressed", 0) * 1.0)
        ) / total
    return {
        "schema": "deterministic_repair_outcome_tracking_v1",
        "allowed_outcome_labels": list(STANDARD_OUTCOME_LABELS),
        "counts_by_outcome": counts,
        "total": total,
        "recommendation_score": round(recommendation_score, 3),
    }


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


def build_negative_learning_model(
    *,
    memory_entries: list[dict[str, Any]],
    min_negative_count: int = 2,
) -> dict[str, Any]:
    negative_counts: dict[str, dict[str, int]] = {}
    for entry in memory_entries:
        procedure_id = str(entry.get("procedure_id") or "unknown_procedure")
        outcome = str(entry.get("outcome_label") or "failed")
        if outcome not in {"failed", "regressed"}:
            continue
        bucket = negative_counts.setdefault(procedure_id, {"failed": 0, "regressed": 0, "total_negative": 0})
        bucket[outcome] = bucket.get(outcome, 0) + 1
        bucket["total_negative"] += 1

    anti_patterns: list[dict[str, Any]] = []
    for procedure_id, counts in negative_counts.items():
        if counts["total_negative"] < int(min_negative_count):
            continue
        severity = "high" if counts.get("regressed", 0) > 0 else "medium"
        anti_patterns.append(
            {
                "procedure_id": procedure_id,
                "negative_counts": counts,
                "severity": severity,
                "recommended_action": "block_for_review" if severity == "high" else "deprioritize",
            }
        )
    return {
        "schema": "deterministic_negative_learning_v1",
        "anti_patterns": anti_patterns,
        "tracked_negative_outcomes": ["failed", "regressed"],
        "min_negative_count": int(min_negative_count),
    }


def build_success_weighted_repair_recommendations(
    *,
    repair_catalog: dict[str, Any],
    signature_matching: dict[str, Any],
    current_environment_facts: dict[str, Any],
    memory_entries: list[dict[str, Any]],
    negative_learning_model: dict[str, Any],
    top_k: int = 3,
) -> dict[str, Any]:
    top_matches = list(signature_matching.get("matches") or [])
    match_by_problem_class = {
        str(match.get("problem_class") or ""): float(match.get("score") or 0.0)
        for match in top_matches
    }
    anti_patterns = {
        str(item.get("procedure_id") or ""): item
        for item in list(negative_learning_model.get("anti_patterns") or [])
    }
    ranked: list[dict[str, Any]] = []
    for entry in list(repair_catalog.get("entries") or []):
        procedure = dict(entry.get("procedure") or {})
        procedure_id = str(procedure.get("id") or "")
        problem_class = str(entry.get("problem_class") or "")
        safety_class = str(procedure.get("safety_class") or "safe")
        relevant_history = [item for item in memory_entries if str(item.get("procedure_id") or "") == procedure_id]
        successful = [item for item in relevant_history if str(item.get("outcome_label") or "") == "succeeded"]
        partial = [item for item in relevant_history if str(item.get("outcome_label") or "") == "partially_helped"]
        total_history = len(relevant_history)
        success_rate = ((len(successful) + (len(partial) * 0.5)) / total_history) if total_history > 0 else 0.0

        similarity_scores: list[float] = []
        for history_entry in relevant_history:
            history_env = dict(history_entry.get("environment_facts") or {})
            similarity = compute_environment_similarity(
                current_environment_facts=current_environment_facts,
                reference_environment_facts=history_env,
            )
            similarity_scores.append(float(similarity["score"]))
        similarity_score = (sum(similarity_scores) / len(similarity_scores)) if similarity_scores else 0.0
        signature_score = match_by_problem_class.get(problem_class, 0.0)

        anti_pattern = anti_patterns.get(procedure_id)
        negative_penalty = 0.0
        blocked_by_negative_learning = False
        if anti_pattern:
            severity = str(anti_pattern.get("severity") or "medium")
            negative_penalty = 0.6 if severity == "high" else 0.25
            blocked_by_negative_learning = severity == "high"

        weighted_score = (signature_score * 0.55) + (success_rate * 0.3) + (similarity_score * 0.15) - negative_penalty
        bounded_score = round(max(0.0, min(1.0, weighted_score)), 3)
        requires_approval = safety_class in {"review_first", "high_risk"}

        ranked.append(
            {
                "procedure_id": procedure_id,
                "problem_class": problem_class,
                "safety_class": safety_class,
                "weighted_score": bounded_score,
                "score_components": {
                    "signature_score": round(signature_score, 3),
                    "success_rate": round(success_rate, 3),
                    "environment_similarity": round(similarity_score, 3),
                    "negative_penalty": round(negative_penalty, 3),
                },
                "requires_approval": requires_approval,
                "blocked_by_negative_learning": blocked_by_negative_learning,
                "safety_override": requires_approval or blocked_by_negative_learning,
                "explanation": (
                    "Ranking combines signature match, historical success and environment similarity; "
                    "safety and negative-learning guardrails remain enforced."
                ),
            }
        )
    ranked.sort(key=lambda item: (-float(item["weighted_score"]), item["procedure_id"]))
    return {
        "schema": "deterministic_success_weighted_recommendation_v1",
        "ranked_recommendations": ranked[: max(1, int(top_k))],
        "safety_override_rule": "ranking_never_overrides_approval_or_negative_learning_blocks",
    }


def classify_repair_action_safety(*, step: dict[str, Any], procedure_safety_class: str) -> str:
    if not bool(step.get("mutation_candidate")):
        return "inspect_only"
    if procedure_safety_class == "high_risk":
        return "high_risk"
    if procedure_safety_class == "review_first":
        return "confirm_required"
    return "bounded_low_risk"


def _approval_scope_key(*, procedure_id: str, target_scope: str, session_id: str) -> str:
    return f"{procedure_id}|{target_scope}|{session_id}"


def decide_llm_escalation(
    *,
    matching_outcome: dict[str, Any],
    repair_execution_result: dict[str, Any],
    deterministic_paths_exhausted: bool,
) -> dict[str, Any]:
    outcome = str(matching_outcome.get("outcome") or "no_match")
    execution_status = str(repair_execution_result.get("status") or "unknown")
    contradictory = bool(repair_execution_result.get("contradictory_evidence_detected"))
    reasons: list[str] = []
    if outcome == "no_match":
        reasons.append("unknown_signature")
    if outcome == "ambiguous_high_confidence":
        reasons.append("ambiguous_high_confidence")
    if outcome == "low_confidence":
        reasons.append("low_confidence")
    if contradictory:
        reasons.append("contradictory_evidence")
    if deterministic_paths_exhausted:
        reasons.append("exhausted_deterministic_paths")
    if outcome == "single_high_confidence" and execution_status == "completed" and not contradictory:
        reasons = []
    should_escalate = bool(reasons)
    return {
        "schema": "deterministic_llm_escalation_decision_v1",
        "should_escalate": should_escalate,
        "reasons": reasons,
        "matching_outcome": outcome,
        "execution_status": execution_status,
        "audit": {
            "policy_schema": LLM_ESCALATION_POLICY_MODEL["schema"],
            "allowed_reasons": list(LLM_ESCALATION_POLICY_MODEL["allowed_reasons"]),
            "forbidden_when": list(LLM_ESCALATION_POLICY_MODEL["forbidden_when"]),
        },
    }


def build_bounded_escalation_prompt(
    *,
    escalation_decision: dict[str, Any],
    normalized_evidence: dict[str, Any],
    signature_matching: dict[str, Any],
    attempted_paths: list[str],
    confidence_model: dict[str, Any],
) -> dict[str, Any]:
    bounded_evidence = []
    for entry in list(normalized_evidence.get("evidence") or [])[:6]:
        bounded_evidence.append(
            {
                "type": entry.get("type"),
                "source": entry.get("source"),
                "severity": entry.get("severity"),
                "summary": str(entry.get("summary") or "")[:200],
            }
        )
    top_matches = [
        {
            "signature_id": match.get("signature_id"),
            "problem_class": match.get("problem_class"),
            "score": match.get("score"),
        }
        for match in list(signature_matching.get("matches") or [])[:3]
    ]
    return {
        "schema": "deterministic_bounded_llm_escalation_prompt_v1",
        "enabled": bool(escalation_decision.get("should_escalate")),
        "reasons": list(escalation_decision.get("reasons") or []),
        "known_evidence": bounded_evidence,
        "attempted_paths": list(attempted_paths or []),
        "confidence": {
            "score": confidence_model.get("score"),
            "decision": confidence_model.get("decision"),
            "thresholds": confidence_model.get("thresholds"),
        },
        "top_signature_matches": top_matches,
        "constraints": {
            "max_evidence_items": 6,
            "max_chars_per_item": 200,
            "require_structured_proposal_output": True,
        },
    }


def convert_llm_proposal_to_reviewed_procedure(
    *,
    llm_proposal: dict[str, Any],
    environment_facts: dict[str, Any],
) -> dict[str, Any]:
    proposal_steps = list(llm_proposal.get("steps") or [])
    structured_steps: list[dict[str, Any]] = []
    for index, step in enumerate(proposal_steps[:5], start=1):
        text = str(step).strip()
        if not text:
            continue
        structured_steps.append(
            {
                "id": f"llm-proposal-step-{index:02d}",
                "title": text[:180],
                "mutation_candidate": True,
                "requires_review": True,
                "requires_approval": True,
                "execution_allowed": False,
            }
        )
    if not structured_steps:
        structured_steps.append(
            {
                "id": "llm-proposal-step-01",
                "title": "No concrete step supplied; requires operator curation.",
                "mutation_candidate": False,
                "requires_review": True,
                "requires_approval": True,
                "execution_allowed": False,
            }
        )
    return {
        "schema": "deterministic_llm_proposal_conversion_v1",
        "proposal_id": str(llm_proposal.get("proposal_id") or "llm-proposal-unknown"),
        "platform_target": environment_facts.get("platform_target"),
        "review_required": True,
        "approval_required": True,
        "execution_allowed_without_review": False,
        "structured_candidate_procedure": {
            "id": f"reviewed-{str(llm_proposal.get('proposal_id') or 'candidate')}",
            "source": "llm_escalation",
            "steps": structured_steps,
        },
    }


def curate_escalation_feedback(
    *,
    escalation_decision: dict[str, Any],
    proposal_conversion: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    should_curate = bool(escalation_decision.get("should_escalate"))
    outcome_label = str(final_verification.get("outcome_label") or "failed")
    candidates: list[dict[str, Any]] = []
    if should_curate:
        candidates.append(
            {
                "candidate_type": "procedure",
                "source_proposal_id": proposal_conversion.get("proposal_id"),
                "curation_required": True,
                "target_catalog": "deterministic_repair_catalog_v1",
                "outcome_label": outcome_label,
            }
        )
        candidates.append(
            {
                "candidate_type": "signature",
                "source_proposal_id": proposal_conversion.get("proposal_id"),
                "curation_required": True,
                "target_catalog": "deterministic_failure_signature_catalog_v1",
                "outcome_label": outcome_label,
            }
        )
    return {
        "schema": "deterministic_escalation_feedback_curation_v1",
        "should_curate": should_curate,
        "candidates": candidates,
        "explicit_curation_step_required": True,
    }


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


def build_operator_session_summary(
    *,
    diagnosis_run: dict[str, Any],
    matching_outcome: dict[str, Any],
    repair_execution_result: dict[str, Any],
    final_verification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "deterministic_operator_session_summary_v1",
        "current_state": final_verification.get("outcome_label"),
        "detected_signature_class": matching_outcome.get("best_problem_class"),
        "chosen_path": {
            "diagnosis_playbook": diagnosis_run.get("playbook_id"),
            "procedure_id": repair_execution_result.get("procedure_id"),
            "execution_status": repair_execution_result.get("status"),
        },
        "verification_status": final_verification.get("verification_summary"),
        "compact_view": True,
    }


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


def build_operator_proposal_preview(
    *,
    repair_preview: dict[str, Any],
    selected_catalog_entry: dict[str, Any],
) -> dict[str, Any]:
    procedure = dict(selected_catalog_entry.get("procedure") or {})
    steps = list(procedure.get("steps") or [])
    preview_steps = [
        {
            "id": step.get("id"),
            "title": step.get("title"),
            "mutation_candidate": bool(step.get("mutation_candidate")),
            "expected_verification": "step_verification_required",
        }
        for step in steps
    ]
    return {
        "schema": "deterministic_operator_proposal_preview_v1",
        "procedure_id": repair_preview.get("procedure_id"),
        "problem_class": repair_preview.get("problem_class"),
        "steps": preview_steps,
        "approval_decision_ready": True,
        "compact_view": True,
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
    repair_catalog = build_initial_repair_procedure_catalog(signature_catalog=signature_catalog)
    selected_catalog_entry = select_repair_procedure_from_catalog(
        repair_catalog=repair_catalog,
        matching_outcome=matching_outcome,
    )
    repair_preview = build_repair_procedure_preview(
        selected_catalog_entry=selected_catalog_entry,
        matching_outcome=matching_outcome,
    )
    execution_session_id = "deterministic-repair-session-v1"
    execution_target_scope = "service_runtime"
    approval_scope = _approval_scope_key(
        procedure_id=str((selected_catalog_entry.get("procedure") or {}).get("id") or "unknown_procedure"),
        target_scope=execution_target_scope,
        session_id=execution_session_id,
    )
    repair_execution_dry_run = execute_repair_procedure(
        selected_catalog_entry=selected_catalog_entry,
        normalized_evidence=normalized_evidence,
        environment_facts=environment_facts,
        dry_run=True,
        approval_policy={"approved_mutations": False},
        session_id=execution_session_id,
        target_scope=execution_target_scope,
    )
    repair_execution_apply = execute_repair_procedure(
        selected_catalog_entry=selected_catalog_entry,
        normalized_evidence=normalized_evidence,
        environment_facts=environment_facts,
        dry_run=False,
        approval_policy={
            "approved_mutations": True,
            "approved_scopes": [approval_scope],
        },
        session_id=execution_session_id,
        target_scope=execution_target_scope,
    )
    final_verification = verify_final_repair_outcome(
        execution_result=repair_execution_apply,
        normalized_evidence=normalized_evidence,
        matching_outcome=matching_outcome,
    )
    recovery_hints = build_recovery_hint_bundle(
        selected_catalog_entry=selected_catalog_entry,
        execution_result=repair_execution_apply,
    )
    memory_entry = build_repair_outcome_memory_entry(
        signature_matching=signature_matching,
        selected_catalog_entry=selected_catalog_entry,
        environment_facts=environment_facts,
        execution_result=repair_execution_apply,
        final_verification=final_verification,
    )
    outcome_tracking = track_repair_outcomes([memory_entry])
    environment_similarity = compute_environment_similarity(
        current_environment_facts=environment_facts,
        reference_environment_facts=dict(memory_entry.get("environment_facts") or {}),
    )
    negative_learning_model = build_negative_learning_model(memory_entries=[memory_entry])
    success_weighted_recommendations = build_success_weighted_repair_recommendations(
        repair_catalog=repair_catalog,
        signature_matching=signature_matching,
        current_environment_facts=environment_facts,
        memory_entries=[memory_entry],
        negative_learning_model=negative_learning_model,
    )
    deterministic_paths_exhausted = (
        matching_outcome.get("outcome") in {"no_match", "low_confidence", "ambiguous_high_confidence"}
        and str(repair_execution_apply.get("status") or "") != "completed"
    )
    llm_escalation_decision = decide_llm_escalation(
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        deterministic_paths_exhausted=bool(deterministic_paths_exhausted),
    )
    llm_escalation_prompt = build_bounded_escalation_prompt(
        escalation_decision=llm_escalation_decision,
        normalized_evidence=normalized_evidence,
        signature_matching=signature_matching,
        attempted_paths=[
            str(diagnosis_run.get("playbook_id") or ""),
            str((selected_catalog_entry.get("procedure") or {}).get("id") or ""),
        ],
        confidence_model=confidence,
    )
    llm_proposal_conversion = convert_llm_proposal_to_reviewed_procedure(
        llm_proposal={
            "proposal_id": "llm-repair-proposal-v1",
            "steps": [
                "Collect additional bounded evidence for ambiguous branch",
                "Prepare reviewed repair candidate with explicit approval gate",
            ],
        },
        environment_facts=environment_facts,
    )
    escalation_feedback_curation = curate_escalation_feedback(
        escalation_decision=llm_escalation_decision,
        proposal_conversion=llm_proposal_conversion,
        final_verification=final_verification,
    )
    repair_audit_chain = build_repair_audit_chain(
        diagnosis_run=diagnosis_run,
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        final_verification=final_verification,
        llm_escalation_decision=llm_escalation_decision,
    )
    operator_session_summary = build_operator_session_summary(
        diagnosis_run=diagnosis_run,
        matching_outcome=matching_outcome,
        repair_execution_result=repair_execution_apply,
        final_verification=final_verification,
    )
    path_visibility = build_path_visibility(
        llm_escalation_decision=llm_escalation_decision,
        matching_outcome=matching_outcome,
    )
    operator_proposal_preview = build_operator_proposal_preview(
        repair_preview=repair_preview,
        selected_catalog_entry=selected_catalog_entry,
    )
    repair_history_view = build_repair_history_inspection_view(
        memory_entries=[memory_entry],
        filter_problem_class=selected_problem_class,
        filter_platform_target=(str(environment_facts.get("platform_target")) if environment_facts.get("platform_target") else None),
    )
    golden_path_examples = build_golden_path_examples()
    rollout_plan = build_rollout_plan()
    test_coverage_manifest = build_test_coverage_manifest()
    unsafe_action_guardrails = evaluate_unsafe_action_guardrails(
        proposed_actions=[
            str(step.get("title") or "")
            for step in list((selected_catalog_entry.get("procedure") or {}).get("steps") or [])
        ]
        + [str(step.get("title") or "") for step in list((llm_proposal_conversion.get("structured_candidate_procedure") or {}).get("steps") or [])],
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
        "repair_catalog": repair_catalog,
        "selected_repair_catalog_entry": selected_catalog_entry,
        "repair_preview": repair_preview,
        "repair_execution": {
            "dry_run": repair_execution_dry_run,
            "apply_run": repair_execution_apply,
        },
        "verification_model": dict(REPAIR_VERIFICATION_MODEL),
        "final_repair_verification": final_verification,
        "recovery_hints": recovery_hints,
        "outcome_memory_model": dict(REPAIR_OUTCOME_MEMORY_MODEL),
        "outcome_memory_entry": memory_entry,
        "outcome_tracking": outcome_tracking,
        "environment_similarity_model": dict(ENVIRONMENT_SIMILARITY_MODEL),
        "environment_similarity": environment_similarity,
        "success_weighted_recommendations": success_weighted_recommendations,
        "negative_learning_model": negative_learning_model,
        "repair_action_safety_classes": dict(REPAIR_ACTION_SAFETY_CLASSES),
        "approval_requirement_model": dict(APPROVAL_REQUIREMENT_MODEL),
        "bounded_execution_policy": {
            "schema": "deterministic_bounded_execution_policy_v1",
            "allow_unbounded_actions": False,
            "allow_unknown_actions": False,
            "enforced": True,
        },
        "llm_escalation_policy": dict(LLM_ESCALATION_POLICY_MODEL),
        "llm_escalation_decision": llm_escalation_decision,
        "llm_escalation_prompt": llm_escalation_prompt,
        "llm_proposal_conversion": llm_proposal_conversion,
        "escalation_feedback_curation": escalation_feedback_curation,
        "repair_audit_chain": repair_audit_chain,
        "unsafe_action_guardrails": unsafe_action_guardrails,
        "operator_views_model": dict(OPERATOR_VIEW_MODEL),
        "operator_session_summary": operator_session_summary,
        "path_visibility": path_visibility,
        "operator_proposal_preview": operator_proposal_preview,
        "repair_history_view": repair_history_view,
        "test_coverage_model": dict(TEST_COVERAGE_MODEL),
        "test_coverage_manifest": test_coverage_manifest,
        "operator_guide_metadata": dict(OPERATOR_GUIDE_METADATA),
        "golden_path_examples": golden_path_examples,
        "rollout_plan_model": dict(ROLLOUT_PLAN_MODEL),
        "rollout_plan": rollout_plan,
    }
