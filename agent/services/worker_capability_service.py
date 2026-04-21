from __future__ import annotations

from agent.common.sgpt import get_cli_backend_capabilities


WORKER_CAPABILITY_PROFILES: dict[str, dict] = {
    "planner": {
        "label": "Planner",
        "roles": ["planner", "hub-worker"],
        "allowed_scopes": ["goal_planning", "task_breakdown", "read_context"],
        "tool_classes": ["read", "planning"],
        "limits": ["no_direct_worker_delegation", "no_unreviewed_mutation"],
        "governance_fit": ["safe", "balanced", "strict"],
    },
    "coder": {
        "label": "Coder",
        "roles": ["developer", "worker"],
        "allowed_scopes": ["code_change", "test_execution", "artifact_creation"],
        "tool_classes": ["read", "write", "terminal"],
        "limits": ["hub_assigned_tasks_only", "policy_gated_terminal", "review_required_for_high_risk"],
        "governance_fit": ["balanced", "strict"],
    },
    "reviewer": {
        "label": "Reviewer",
        "roles": ["reviewer", "qa", "security"],
        "allowed_scopes": ["review", "verification", "risk_assessment"],
        "tool_classes": ["read", "analysis"],
        "limits": ["no_direct_mutation", "evidence_required"],
        "governance_fit": ["safe", "balanced", "strict"],
    },
    "operator": {
        "label": "Operator",
        "roles": ["ops", "release"],
        "allowed_scopes": ["diagnostics", "release_readiness", "runtime_health"],
        "tool_classes": ["read", "terminal", "admin"],
        "limits": ["admin_required_for_mutation", "audit_required", "least_privilege"],
        "governance_fit": ["balanced", "strict"],
    },
}


class WorkerCapabilityService:
    """Maps worker tooling to hub-visible capability descriptors."""

    def build_tooling_capability_map(self) -> dict[str, dict]:
        capabilities = get_cli_backend_capabilities()
        tool_keys = ("sgpt", "codex", "opencode", "aider", "mistral_code")
        mapping: dict[str, dict] = {}
        for key in tool_keys:
            info = capabilities.get(key) or {}
            mapping[key] = {
                "tool": key,
                "available": bool(info.get("available")),
                "supports_model_selection": bool(info.get("supports_model_selection")),
                "supported_options": list(info.get("supported_options") or []),
                "install_hint": info.get("install_hint"),
            }
        return mapping

    def build_worker_capability_profiles(self) -> dict[str, dict]:
        return {
            key: {
                **dict(profile),
                "profile": key,
                "contract_version": "v1",
                "orchestration_boundary": "hub_owned_task_queue",
                "worker_rule": "execute_delegated_work_only",
            }
            for key, profile in sorted(WORKER_CAPABILITY_PROFILES.items())
        }


worker_capability_service = WorkerCapabilityService()


def get_worker_capability_service() -> WorkerCapabilityService:
    return worker_capability_service
