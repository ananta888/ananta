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
