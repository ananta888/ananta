from __future__ import annotations

import hashlib
from typing import Any

from agent.services.admin_repair_taxonomy import (
    ALLOWED_EVIDENCE_SOURCES,
    SUPPORTED_PLATFORM_PROFILES,
)
from agent.services.admin_repair_normalizers import _normalize_platform_target


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
