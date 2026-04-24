from __future__ import annotations

from typing import Any


SUPPORTED_PLATFORM_PROFILES: dict[str, dict[str, Any]] = {
    "windows11": {
        "supported": True,
        "label": "Windows 11",
        "shell_family": "powershell",
        "package_manager": "winget_or_choco",
        "service_manager": "windows_service_control_manager",
    },
    "ubuntu": {
        "supported": True,
        "label": "Ubuntu",
        "shell_family": "bash",
        "package_manager": "apt_dpkg",
        "service_manager": "systemd",
    },
    "unknown": {
        "supported": False,
        "label": "Unknown",
        "shell_family": "unknown",
        "package_manager": "unknown",
        "service_manager": "unknown",
    },
}

ALLOWED_EVIDENCE_SOURCES: tuple[str, ...] = (
    "error_logs",
    "service_status",
    "runtime_state",
    "package_state",
    "path_state",
    "permission_state",
    "port_state",
    "container_state",
)

KRITIS_HOOK_FIELDS: tuple[str, ...] = (
    "risk_class",
    "requires_approval",
    "dry_run_supported",
    "verification_required",
    "mutation_candidate",
    "evidence_sources",
    "execution_scope",
    "audit_hint",
    "repair_action_class",
    "affected_targets",
    "expected_verification",
)

PROBLEM_TAXONOMY: dict[str, dict[str, Any]] = {
    "package_runtime": {
        "keywords": ("package", "apt", "dpkg", "pip", "npm", "dependency", "module"),
        "cause_hints": [
            "runtime_or_package_dependency_missing",
            "incompatible_package_or_version",
            "partial_package_installation_state",
        ],
        "verification_hint": "target_runtime_and_package_commands_return_expected_versions",
    },
    "path_resolution": {
        "keywords": ("path", "not found", "unknown command", "executable"),
        "cause_hints": [
            "path_variable_missing_required_entry",
            "binary_installed_but_not_resolvable",
            "shell_profile_missing_path_export",
        ],
        "verification_hint": "required_commands_resolve_in_target_shell",
    },
    "permissions": {
        "keywords": ("permission", "access denied", "forbidden", "unauthorized", "chmod", "ownership"),
        "cause_hints": [
            "missing_required_file_or_directory_permissions",
            "runtime_user_does_not_own_required_targets",
            "policy_or_acl_blocks_required_operation",
        ],
        "verification_hint": "required_operations_succeed_without_permission_errors",
    },
    "service_health": {
        "keywords": ("service", "restart loop", "failed", "unhealthy", "systemd", "scm"),
        "cause_hints": [
            "service_configuration_invalid_or_incomplete",
            "service_dependency_not_available",
            "service_runtime_crash_after_start",
        ],
        "verification_hint": "service_reports_running_and_health_signals_are_stable",
    },
    "port_binding": {
        "keywords": ("port", "bind", "address already in use", "listen"),
        "cause_hints": [
            "required_port_already_bound_by_other_process",
            "service_bind_configuration_incorrect",
            "network_rule_prevents_expected_binding",
        ],
        "verification_hint": "required_port_binding_and_connectivity_checks_pass",
    },
    "container_runtime": {
        "keywords": ("docker", "compose", "container", "image", "wsl"),
        "cause_hints": [
            "container_runtime_not_ready",
            "compose_configuration_or_dependency_error",
            "image_or_volume_state_inconsistent",
        ],
        "verification_hint": "container_stack_starts_and_required_services_reach_healthy_state",
    },
}

ACTION_RISK_CLASS: dict[str, str] = {
    "inspect_state": "low",
    "preview_mutation_plan": "low",
    "restart_service": "medium",
    "package_state_repair": "medium",
    "path_repair": "medium",
    "permission_fix": "high",
    "port_conflict_resolution": "medium",
    "container_runtime_repair": "medium",
    "bounded_runtime_reset": "high",
    "verification_check": "low",
    "diagnosis_only_followup": "low",
}


def _to_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_platform_target(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "auto": "unknown",
        "windows": "windows11",
        "windows11": "windows11",
        "win11": "windows11",
        "ubuntu": "ubuntu",
        "ubuntu22": "ubuntu",
        "ubuntu24": "ubuntu",
        "linux": "ubuntu",
    }
    return aliases.get(raw, "unknown")


def _normalize_execution_scope(value: Any, *, platform_supported: bool) -> str:
    raw = str(value or "").strip().lower()
    if raw not in {"diagnosis_only", "bounded_repair"}:
        return "bounded_repair" if platform_supported else "diagnosis_only"
    if raw == "bounded_repair" and not platform_supported:
        return "diagnosis_only"
    return raw


def _normalize_evidence_sources(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = []

    normalized: list[str] = []
    for item in raw_items:
        if not item:
            continue
        if item not in ALLOWED_EVIDENCE_SOURCES:
            continue
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        normalized = ["error_logs", "service_status", "runtime_state"]
    return normalized[:8]


def _normalize_targets(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return [item for item in items if item][:6]


def _problem_match_score(problem_class: str, text: str) -> int:
    keyword_hits = 0
    for keyword in PROBLEM_TAXONOMY[problem_class]["keywords"]:
        if keyword in text:
            keyword_hits += 1
    return keyword_hits


def _classify_problem(symptom: str, targets: list[str]) -> tuple[str, float]:
    text = " ".join([symptom.strip().lower(), " ".join(targets).lower()]).strip()
    if not text:
        return "service_health", 0.4

    scores = {problem_class: _problem_match_score(problem_class, text) for problem_class in PROBLEM_TAXONOMY}
    best_class = max(scores, key=scores.get)
    best_score = scores[best_class]
    if best_score <= 0:
        return "service_health", 0.45
    confidence = min(0.92, 0.55 + (best_score * 0.12))
    return best_class, round(confidence, 2)


def _advisory_class_for_action(*, risk_class: str, mutation_candidate: bool) -> str:
    if risk_class == "high" and mutation_candidate:
        return "blocked"
    if mutation_candidate or risk_class == "medium":
        return "confirm-required"
    return "allow"


def _build_repair_action(
    *,
    action_id: str,
    title: str,
    repair_action_class: str,
    affected_targets: list[str],
    evidence_sources: list[str],
    execution_scope: str,
    mutation_candidate: bool,
    dry_run_supported: bool,
    expected_verification: str,
) -> dict[str, Any]:
    risk_class = ACTION_RISK_CLASS.get(repair_action_class, "medium")
    advisory_class = _advisory_class_for_action(risk_class=risk_class, mutation_candidate=mutation_candidate)
    return {
        "id": action_id,
        "title": title,
        "risk_class": risk_class,
        "requires_approval": advisory_class in {"confirm-required", "blocked"},
        "dry_run_supported": dry_run_supported,
        "verification_required": True,
        "mutation_candidate": mutation_candidate,
        "evidence_sources": list(evidence_sources),
        "execution_scope": execution_scope,
        "audit_hint": f"admin_repair:{repair_action_class}",
        "repair_action_class": repair_action_class,
        "affected_targets": list(affected_targets),
        "expected_verification": expected_verification,
        "advisory_class": advisory_class,
    }


def _resolve_platform_detection(mode_data: dict[str, Any]) -> dict[str, Any]:
    target = _normalize_platform_target(mode_data.get("platform_target"))
    profile = SUPPORTED_PLATFORM_PROFILES.get(target, SUPPORTED_PLATFORM_PROFILES["unknown"])
    return {
        "platform_target": target,
        "platform_label": profile["label"],
        "supported": bool(profile["supported"]),
        "source": "mode_data",
    }


def _build_environment_summary(platform_detection: dict[str, Any], mode_data: dict[str, Any]) -> dict[str, Any]:
    profile = SUPPORTED_PLATFORM_PROFILES.get(
        str(platform_detection.get("platform_target") or "unknown"),
        SUPPORTED_PLATFORM_PROFILES["unknown"],
    )
    return {
        "platform_target": platform_detection.get("platform_target"),
        "supported": bool(platform_detection.get("supported")),
        "shell_family": profile["shell_family"],
        "package_manager": profile["package_manager"],
        "service_manager": profile["service_manager"],
        "runtime_basics": ["python", "node", "docker", "git"],
        "runtime_target": str(mode_data.get("runtime_target") or "").strip() or None,
    }


def _build_diagnosis_artifact(
    *,
    issue_symptom: str,
    affected_targets: list[str],
    evidence_sources: list[str],
) -> dict[str, Any]:
    problem_class, confidence = _classify_problem(issue_symptom, affected_targets)
    taxonomy = PROBLEM_TAXONOMY[problem_class]
    return {
        "schema": "admin_repair_diagnosis_v1",
        "problem_class": problem_class,
        "confidence": confidence,
        "likely_causes": list(taxonomy["cause_hints"]),
        "evidence_sources": list(evidence_sources),
        "evidence_links": [
            {"source": source, "reference": f"evidence:{source}", "collection_mode": "bounded"}
            for source in evidence_sources
        ],
        "next_steps": [
            "review_environment_summary",
            "validate_selected_evidence_sources",
            "confirm_bounded_repair_plan_or_keep_diagnosis_only",
        ],
    }


def _repair_action_for_problem(problem_class: str) -> str:
    return {
        "service_health": "restart_service",
        "package_runtime": "package_state_repair",
        "path_resolution": "path_repair",
        "permissions": "permission_fix",
        "port_binding": "port_conflict_resolution",
        "container_runtime": "container_runtime_repair",
    }.get(problem_class, "bounded_runtime_reset")


def _build_repair_plan(
    *,
    diagnosis_artifact: dict[str, Any],
    affected_targets: list[str],
    evidence_sources: list[str],
    execution_scope: str,
    dry_run_default: bool,
) -> dict[str, Any]:
    problem_class = str(diagnosis_artifact.get("problem_class") or "service_health")
    verification_hint = str(PROBLEM_TAXONOMY.get(problem_class, {}).get("verification_hint") or "verification_checks_pass")
    actions: list[dict[str, Any]] = [
        _build_repair_action(
            action_id="repair-action-01",
            title="Collect bounded inspection snapshot",
            repair_action_class="inspect_state",
            affected_targets=affected_targets,
            evidence_sources=evidence_sources,
            execution_scope=execution_scope,
            mutation_candidate=False,
            dry_run_supported=True,
            expected_verification="inspection_snapshot_available",
        ),
        _build_repair_action(
            action_id="repair-action-02",
            title="Generate dry-run preview for selected repair path",
            repair_action_class="preview_mutation_plan",
            affected_targets=affected_targets,
            evidence_sources=evidence_sources,
            execution_scope=execution_scope,
            mutation_candidate=False,
            dry_run_supported=True,
            expected_verification="preview_contains_explicit_steps_and_targets",
        ),
    ]

    if execution_scope == "bounded_repair":
        action_class = _repair_action_for_problem(problem_class)
        actions.append(
            _build_repair_action(
                action_id="repair-action-03",
                title="Execute bounded repair step after explicit confirmation",
                repair_action_class=action_class,
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=True,
                dry_run_supported=True,
                expected_verification=verification_hint,
            )
        )
        actions.append(
            _build_repair_action(
                action_id="repair-action-04",
                title="Run post-repair verification checks",
                repair_action_class="verification_check",
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=False,
                dry_run_supported=False,
                expected_verification=verification_hint,
            )
        )
    else:
        actions.append(
            _build_repair_action(
                action_id="repair-action-03",
                title="Emit diagnosis-only follow-up recommendation",
                repair_action_class="diagnosis_only_followup",
                affected_targets=affected_targets,
                evidence_sources=evidence_sources,
                execution_scope=execution_scope,
                mutation_candidate=False,
                dry_run_supported=True,
                expected_verification="operator_review_acknowledges_next_steps",
            )
        )

    return {
        "schema": "admin_repair_plan_v1",
        "dry_run_default": dry_run_default,
        "execution_scope": execution_scope,
        "steps": actions,
        "result_states": ["resolved", "improved", "unchanged", "regressed"],
        "advisory_policy": "classification_is_hook_ready_and_not_full_kritis_enforcement",
    }


def build_admin_repair_mode_data(mode_data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(mode_data or {})
    issue_symptom = str(normalized.get("issue_symptom") or "").strip() or "unspecified_admin_issue"
    platform_detection = _resolve_platform_detection(normalized)
    execution_scope = _normalize_execution_scope(
        normalized.get("execution_scope"),
        platform_supported=bool(platform_detection.get("supported")),
    )
    dry_run_default = _to_bool(normalized.get("dry_run"), default=True)
    evidence_sources = _normalize_evidence_sources(normalized.get("evidence_sources"))
    affected_targets = _normalize_targets(normalized.get("affected_targets"))
    if not affected_targets:
        affected_targets = ["service_runtime"]

    diagnosis_artifact = _build_diagnosis_artifact(
        issue_symptom=issue_symptom,
        affected_targets=affected_targets,
        evidence_sources=evidence_sources,
    )
    repair_plan = _build_repair_plan(
        diagnosis_artifact=diagnosis_artifact,
        affected_targets=affected_targets,
        evidence_sources=evidence_sources,
        execution_scope=execution_scope,
        dry_run_default=dry_run_default,
    )

    normalized["issue_symptom"] = issue_symptom
    normalized["platform_target"] = platform_detection["platform_target"]
    normalized["execution_scope"] = execution_scope
    normalized["dry_run"] = dry_run_default
    normalized["evidence_sources"] = evidence_sources
    normalized["affected_targets"] = affected_targets
    normalized["platform_detection"] = platform_detection
    normalized["environment_summary"] = _build_environment_summary(platform_detection, normalized)
    normalized["evidence_contract"] = {
        "allowed_sources": list(ALLOWED_EVIDENCE_SOURCES),
        "selected_sources": evidence_sources,
        "bounded_collection": {
            "max_items_per_source": 50,
            "max_chars_per_item": 4000,
            "sensitive_fields_redacted": True,
        },
    }
    normalized["problem_taxonomy"] = {
        "classes": list(PROBLEM_TAXONOMY.keys()),
        "mapped_problem_class": diagnosis_artifact["problem_class"],
    }
    normalized["diagnosis_artifact"] = diagnosis_artifact
    normalized["repair_plan"] = repair_plan
    normalized["kritis_hook_fields"] = list(KRITIS_HOOK_FIELDS)
    normalized["kritis_enforcement"] = "deferred"
    return normalized


def render_admin_repair_goal(mode_data: dict[str, Any]) -> str:
    issue_symptom = str(mode_data.get("issue_symptom") or "unspecified_admin_issue")
    platform = str((mode_data.get("platform_detection") or {}).get("platform_label") or mode_data.get("platform_target") or "Unknown")
    scope = str(mode_data.get("execution_scope") or "diagnosis_only")
    diagnosis_class = str((mode_data.get("diagnosis_artifact") or {}).get("problem_class") or "service_health")
    evidence_sources = list(mode_data.get("evidence_sources") or [])
    dry_run = bool(mode_data.get("dry_run", True))
    return (
        f"Bearbeite einen Admin-Repair-Fall als Shared Foundation: {issue_symptom}. "
        f"Plattformziel: {platform}. "
        "Fokussiere auf bounded diagnosis, advisory repair classification und verifizierbare Outputs; "
        "dieser Modus ist nicht voll KRITIS-gehaertet. "
        f"Execution Scope: {scope}. Dry-run default: {dry_run}. "
        f"Diagnoseklasse: {diagnosis_class}. "
        f"Evidenzquellen: {', '.join(evidence_sources)}."
    )
