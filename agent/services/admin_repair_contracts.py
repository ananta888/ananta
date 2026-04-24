from __future__ import annotations

import hashlib
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

ACTION_BEHAVIOR: dict[str, dict[str, Any]] = {
    "inspect_state": {"rollback_supported": True, "rollback_hint": "no_mutation"},
    "preview_mutation_plan": {"rollback_supported": True, "rollback_hint": "preview_only"},
    "restart_service": {"rollback_supported": True, "rollback_hint": "restart_previous_service_state_if_known"},
    "package_state_repair": {"rollback_supported": False, "rollback_hint": "manual_package_state_recovery_required"},
    "path_repair": {"rollback_supported": True, "rollback_hint": "restore_previous_path_entries_from_backup"},
    "permission_fix": {"rollback_supported": False, "rollback_hint": "manual_permission_recovery_may_be_required"},
    "port_conflict_resolution": {"rollback_supported": True, "rollback_hint": "restore_previous_service_binding_configuration"},
    "container_runtime_repair": {"rollback_supported": True, "rollback_hint": "restart_previous_stack_definition"},
    "bounded_runtime_reset": {"rollback_supported": False, "rollback_hint": "manual_runtime_rebootstrap_required"},
    "verification_check": {"rollback_supported": True, "rollback_hint": "verification_step_only"},
    "diagnosis_only_followup": {"rollback_supported": True, "rollback_hint": "no_mutation"},
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
    behavior = ACTION_BEHAVIOR.get(
        repair_action_class,
        {"rollback_supported": False, "rollback_hint": "manual_recovery_required"},
    )
    advisory_class = _advisory_class_for_action(risk_class=risk_class, mutation_candidate=mutation_candidate)
    caution_level = "high" if (risk_class == "high" or advisory_class == "blocked") else ("medium" if advisory_class == "confirm-required" else "low")
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
        "rollback_supported": bool(behavior["rollback_supported"]),
        "rollback_hint": str(behavior["rollback_hint"]),
        "caution_level": caution_level,
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


def _build_platform_evidence_adapters(
    *,
    platform_target: str,
    evidence_sources: list[str],
    issue_symptom: str,
) -> dict[str, Any]:
    adapters = {
        "windows11": {
            "adapter_id": "windows11_evidence_adapter_v1",
            "platform": "windows11",
            "supports_live_collection": False,
            "default_collection_mode": "fixture",
            "supported_sources": list(ALLOWED_EVIDENCE_SOURCES),
            "collector_hints": {
                "error_logs": "windows:eventlog:application",
                "service_status": "windows:scm:services",
                "runtime_state": "windows:runtime:python-node-docker",
                "package_state": "windows:packages:winget-choco",
                "path_state": "windows:env:path",
                "permission_state": "windows:acl:filesystem",
                "port_state": "windows:netstat:ports",
                "container_state": "windows:docker:compose-status",
            },
        },
        "ubuntu": {
            "adapter_id": "ubuntu_evidence_adapter_v1",
            "platform": "ubuntu",
            "supports_live_collection": False,
            "default_collection_mode": "fixture",
            "supported_sources": list(ALLOWED_EVIDENCE_SOURCES),
            "collector_hints": {
                "error_logs": "ubuntu:journalctl:errors",
                "service_status": "ubuntu:systemctl:services",
                "runtime_state": "ubuntu:runtime:python-node-docker",
                "package_state": "ubuntu:packages:apt-dpkg",
                "path_state": "ubuntu:shell:path",
                "permission_state": "ubuntu:permissions:filesystem",
                "port_state": "ubuntu:ss:ports",
                "container_state": "ubuntu:docker:compose-status",
            },
        },
    }
    selected = adapters.get(platform_target)
    selected_sources = [source for source in evidence_sources if source in ALLOWED_EVIDENCE_SOURCES]
    collection_errors: list[dict[str, Any]] = []
    if not selected:
        collection_errors.append(
            {
                "severity": "info",
                "code": "unsupported_platform_adapter",
                "message": "No live adapter for platform target; using diagnosis-only fixture baseline.",
            }
        )
    if "error_logs" not in selected_sources:
        collection_errors.append(
            {
                "severity": "warning",
                "code": "missing_core_signal",
                "message": "error_logs not selected; diagnosis confidence may be reduced.",
            }
        )
    return {
        "adapters": adapters,
        "selected_adapter": selected,
        "selected_sources": selected_sources,
        "collection_mode": (selected or {}).get("default_collection_mode", "fixture"),
        "collection_errors": collection_errors,
        "symptom_snapshot": issue_symptom[:240],
    }


def _build_platform_playbooks(*, platform_target: str, problem_class: str) -> dict[str, Any]:
    windows_playbooks = [
        {
            "id": "windows-runtime-path-repair",
            "focus": "runtime/path",
            "supported_problem_classes": ["package_runtime", "path_resolution"],
            "bounded_mutation": True,
            "affected_targets_hint": ["runtime", "path"],
        },
        {
            "id": "windows-service-repair",
            "focus": "service-health",
            "supported_problem_classes": ["service_health", "port_binding"],
            "bounded_mutation": True,
            "affected_targets_hint": ["service", "port"],
        },
        {
            "id": "windows-container-runtime-repair",
            "focus": "container-runtime",
            "supported_problem_classes": ["container_runtime"],
            "bounded_mutation": True,
            "affected_targets_hint": ["docker", "compose", "wsl"],
        },
    ]
    ubuntu_playbooks = [
        {
            "id": "ubuntu-package-runtime-repair",
            "focus": "apt-dpkg-runtime",
            "supported_problem_classes": ["package_runtime"],
            "bounded_mutation": True,
            "affected_targets_hint": ["apt", "dpkg", "runtime"],
        },
        {
            "id": "ubuntu-service-repair",
            "focus": "systemd-service-health",
            "supported_problem_classes": ["service_health", "port_binding"],
            "bounded_mutation": True,
            "affected_targets_hint": ["systemd", "service", "port"],
        },
        {
            "id": "ubuntu-toolchain-runtime-repair",
            "focus": "runtime-toolchain",
            "supported_problem_classes": ["path_resolution", "permissions", "container_runtime"],
            "bounded_mutation": True,
            "affected_targets_hint": ["runtime", "permissions", "docker"],
        },
    ]
    by_platform = {"windows11": windows_playbooks, "ubuntu": ubuntu_playbooks}
    selected = by_platform.get(platform_target, [])
    recommended = [playbook for playbook in selected if problem_class in playbook["supported_problem_classes"]]
    if not recommended and selected:
        recommended = [selected[0]]
    return {
        "catalog": by_platform,
        "selected_platform": platform_target,
        "recommended_playbooks": recommended,
    }


def _build_execution_session_template(repair_plan: dict[str, Any]) -> dict[str, Any]:
    steps = list(repair_plan.get("steps") or [])
    session_steps: list[dict[str, Any]] = []
    for index, action in enumerate(steps, start=1):
        step_id = str(action.get("id") or f"repair-action-{index:02d}")
        requires_confirmation = bool(action.get("mutation_candidate")) or bool(action.get("requires_approval"))
        session_steps.append(
            {
                "step_id": step_id,
                "position": index,
                "title": action.get("title"),
                "confirmation_required": requires_confirmation,
                "advisory_class": action.get("advisory_class"),
                "state": "pending",
            }
        )
    return {
        "session_schema": "admin_repair_execution_session_v1",
        "execution_mode": "step_confirmed",
        "allow_unmodeled_actions": False,
        "allow_unrestricted_shell_repair": False,
        "can_stop_between_steps": True,
        "states": ["pending", "confirmed", "executed", "skipped", "blocked", "stopped"],
        "steps": session_steps,
    }


def _build_verification_phase(
    *,
    repair_plan: dict[str, Any],
    diagnosis_artifact: dict[str, Any],
    execution_scope: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for action in list(repair_plan.get("steps") or []):
        if not bool(action.get("verification_required")):
            continue
        checks.append(
            {
                "action_id": action.get("id"),
                "required": True,
                "expected_verification": action.get("expected_verification"),
                "status": "pending",
            }
        )
    result_state = "improved" if execution_scope == "bounded_repair" else "unchanged"
    return {
        "schema": "admin_repair_verification_v1",
        "checks": checks,
        "result_state": result_state,
        "result_state_candidates": ["resolved", "improved", "unchanged", "regressed"],
        "diagnosis_class": diagnosis_artifact.get("problem_class"),
        "note": "command_success_is_not_sufficient_when_verification_required",
    }


def _build_hardening_bridge_contract(repair_plan: dict[str, Any]) -> dict[str, Any]:
    action_ids = [str(action.get("id") or "") for action in list(repair_plan.get("steps") or []) if str(action.get("id") or "")]
    digest = hashlib.sha1("|".join(action_ids).encode("utf-8")).hexdigest()[:12] if action_ids else "empty"
    bridge_actions = []
    for action_id in action_ids:
        bridge_actions.append(
            {
                "action_id": action_id,
                "audit_event_id": f"audit:{action_id}",
                "mutation_gate_key": f"mutation:{action_id}",
                "approval_policy_key": f"approval:{action_id}",
                "sandbox_scope_key": f"sandbox:{action_id}",
            }
        )
    return {
        "schema": "admin_repair_bridge_contract_v1",
        "session_bridge_id": f"admin-repair-bridge-{digest}",
        "bridge_actions": bridge_actions,
        "integration_targets": ["Audit", "MutationGate", "ApprovalPolicy", "Sandboxing"],
        "enforcement_mode": "deferred_kritis_hardening",
    }


def _build_session_trail(
    *,
    evidence_contract: dict[str, Any],
    repair_plan: dict[str, Any],
    execution_session: dict[str, Any],
    verification_phase: dict[str, Any],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = [
        {
            "event": "evidence_collected",
            "sources": list(evidence_contract.get("selected_sources") or []),
            "bounded": True,
        },
        {
            "event": "plan_generated",
            "step_count": len(list(repair_plan.get("steps") or [])),
            "advisory_policy": repair_plan.get("advisory_policy"),
        },
    ]
    for step in list(execution_session.get("steps") or []):
        entries.append(
            {
                "event": "execution_step",
                "step_id": step.get("step_id"),
                "confirmation_required": bool(step.get("confirmation_required")),
                "state": step.get("state"),
            }
        )
    entries.append(
        {
            "event": "verification_phase",
            "check_count": len(list(verification_phase.get("checks") or [])),
            "result_state": verification_phase.get("result_state"),
        }
    )
    return {
        "schema": "admin_repair_session_trail_v1",
        "entries": entries,
        "sensitive_handling": {
            "redaction_enabled": True,
            "note": "sensitive_material_handling_explicit",
        },
    }


def _build_cli_output_sections(
    *,
    diagnosis_artifact: dict[str, Any],
    repair_plan: dict[str, Any],
    verification_phase: dict[str, Any],
) -> dict[str, Any]:
    risk_summary = [
        {
            "action_id": action.get("id"),
            "risk_class": action.get("risk_class"),
            "advisory_class": action.get("advisory_class"),
            "requires_approval": action.get("requires_approval"),
        }
        for action in list(repair_plan.get("steps") or [])
    ]
    return {
        "schema": "admin_repair_cli_output_v1",
        "sections": [
            {
                "id": "diagnosis",
                "title": "Diagnosis",
                "content": {
                    "problem_class": diagnosis_artifact.get("problem_class"),
                    "confidence": diagnosis_artifact.get("confidence"),
                    "likely_causes": diagnosis_artifact.get("likely_causes"),
                },
            },
            {
                "id": "plan",
                "title": "Repair Plan",
                "content": {
                    "execution_scope": repair_plan.get("execution_scope"),
                    "step_count": len(list(repair_plan.get("steps") or [])),
                    "dry_run_default": repair_plan.get("dry_run_default"),
                },
            },
            {
                "id": "risk",
                "title": "Risk and Approval",
                "content": risk_summary,
            },
            {
                "id": "verification",
                "title": "Verification",
                "content": {
                    "result_state": verification_phase.get("result_state"),
                    "checks": verification_phase.get("checks"),
                },
            },
        ],
        "advisory_vs_enforced_visible": True,
    }


def _build_smoke_scenarios(
    *,
    platform_target: str,
    issue_symptom: str,
    evidence_sources: list[str],
    execution_scope: str,
) -> list[dict[str, Any]]:
    base = {
        "scenario_schema": "admin_repair_smoke_scenario_v1",
        "symptom": issue_symptom[:160],
        "evidence_sources": list(evidence_sources),
        "execution_scope": execution_scope,
        "expects_confirmation_gates": True,
    }
    windows_scenario = {
        **base,
        "scenario_id": "windows-fixture-smoke",
        "platform_target": "windows11",
        "fixture_profile": "windows_service_restart_loop",
    }
    ubuntu_scenario = {
        **base,
        "scenario_id": "ubuntu-fixture-smoke",
        "platform_target": "ubuntu",
        "fixture_profile": "ubuntu_service_unhealthy",
    }
    if platform_target == "windows11":
        return [windows_scenario, ubuntu_scenario]
    if platform_target == "ubuntu":
        return [ubuntu_scenario, windows_scenario]
    return [windows_scenario, ubuntu_scenario]


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
    platform_adapters = _build_platform_evidence_adapters(
        platform_target=platform_detection["platform_target"],
        evidence_sources=evidence_sources,
        issue_symptom=issue_symptom,
    )
    platform_playbooks = _build_platform_playbooks(
        platform_target=platform_detection["platform_target"],
        problem_class=str(diagnosis_artifact.get("problem_class") or "service_health"),
    )
    execution_session = _build_execution_session_template(repair_plan)
    verification_phase = _build_verification_phase(
        repair_plan=repair_plan,
        diagnosis_artifact=diagnosis_artifact,
        execution_scope=execution_scope,
    )
    bridge_contract = _build_hardening_bridge_contract(repair_plan)

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
    normalized["platform_evidence_adapters"] = platform_adapters
    normalized["platform_playbooks"] = platform_playbooks
    normalized["execution_session"] = execution_session
    normalized["verification_phase"] = verification_phase
    normalized["bridge_contract"] = bridge_contract
    normalized["session_trail"] = _build_session_trail(
        evidence_contract=normalized["evidence_contract"],
        repair_plan=repair_plan,
        execution_session=execution_session,
        verification_phase=verification_phase,
    )
    normalized["cli_output"] = _build_cli_output_sections(
        diagnosis_artifact=diagnosis_artifact,
        repair_plan=repair_plan,
        verification_phase=verification_phase,
    )
    normalized["smoke_scenarios"] = _build_smoke_scenarios(
        platform_target=platform_detection["platform_target"],
        issue_symptom=issue_symptom,
        evidence_sources=evidence_sources,
        execution_scope=execution_scope,
    )
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
        "Fokussiere auf bounded diagnosis, step-confirmed execution, advisory repair classification und verifizierbare Outputs; "
        "dieser Modus ist nicht voll KRITIS-gehaertet. "
        f"Execution Scope: {scope}. Dry-run default: {dry_run}. "
        f"Diagnoseklasse: {diagnosis_class}. "
        f"Evidenzquellen: {', '.join(evidence_sources)}."
    )
