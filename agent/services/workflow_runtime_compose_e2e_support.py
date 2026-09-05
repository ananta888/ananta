"""Fail-closed policy adapter for the isolated Compose workflow E2E runtime."""

from __future__ import annotations

from agent.config import settings
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime_rollout_service import rollout_scope_from_plan

COMPOSE_E2E_PROJECT_ID = "compose-e2e-chat-process"
COMPOSE_E2E_RUNTIME_CONTEXT = "compose-e2e"
_NATIVE_CAPABILITIES = frozenset(
    {
        "audit",
        "authorization",
        "policy",
        "side_effect_guard",
    }
)


def compose_e2e_test_support_enabled() -> bool:
    """Return whether the exact private Compose-E2E Hub context is active."""

    return bool(
        settings.role == "hub"
        and settings.auth_test_endpoints_enabled
        and settings.workflow_runtime_test_context == COMPOSE_E2E_RUNTIME_CONTEXT
    )


class ComposeE2ERuntimeReleaseAdmission:
    """Admit only the fixed synthetic Native scope in Compose E2E.

    This adapter is deliberately incapable of validating production evidence;
    it merely keeps the functional browser harness independent from a stale or
    absent production release artifact. The exact test context, project,
    runtime, version and governance-only capability set are all mandatory.
    """

    def evaluate(
        self,
        *,
        plan: ExecutionPlan,
        runtime_id: str,
        runtime_version: str,
        required_capabilities: frozenset[str],
    ) -> tuple[bool, str]:
        try:
            scope = rollout_scope_from_plan(plan)
        except (TypeError, ValueError):
            return False, "runtime_release_compose_e2e_scope_invalid"
        allowed = bool(
            compose_e2e_test_support_enabled()
            and scope.project_id == COMPOSE_E2E_PROJECT_ID
            and runtime_id == "ananta-native"
            and runtime_version == "1.0.0"
            and required_capabilities <= _NATIVE_CAPABILITIES
        )
        return (
            (True, "runtime_release_compose_e2e_test_fixture")
            if allowed
            else (False, "runtime_release_compose_e2e_scope_denied")
        )


__all__ = [
    "COMPOSE_E2E_PROJECT_ID",
    "COMPOSE_E2E_RUNTIME_CONTEXT",
    "ComposeE2ERuntimeReleaseAdmission",
    "compose_e2e_test_support_enabled",
]
