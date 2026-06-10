"""Module-level policy constants for the deterministic repair path service.

These constants are shared between the public main module and the 17
themed sub-modules. They live in their own module to avoid circular
imports (the main module imports the sub-modules for delegation; the
sub-modules in turn need these constants).
"""

from __future__ import annotations

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




