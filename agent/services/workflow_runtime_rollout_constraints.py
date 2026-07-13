"""Hub-compiled rollout scope and execution-plan admission constraints.

This module deliberately contains no rollout persistence or promotion logic.
It is the narrow policy-boundary adapter used by selection, promotion and
rollback services before a workflow can be delegated to a worker.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.execution_plan import ExecutionPlan

_RUNTIME_ALIASES = {"native": "ananta-native", "local": "ananta-native"}


def canonical_runtime_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return _RUNTIME_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class WorkflowRolloutScope:
    project_id: str
    tenant_id: str = ""
    profile_id: str = ""
    workflow_id: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WorkflowRolloutScope":
        scope = cls(
            project_id=str(raw.get("project_id") or "").strip(),
            tenant_id=str(raw.get("tenant_id") or "").strip(),
            profile_id=str(raw.get("profile_id") or "").strip(),
            workflow_id=str(raw.get("workflow_id") or "").strip(),
        )
        scope.assert_valid()
        return scope

    @property
    def scope_type(self) -> str:
        if self.workflow_id:
            return "workflow"
        if self.profile_id:
            return "profile"
        if self.tenant_id:
            return "tenant"
        return "project"

    @property
    def scope_key(self) -> str:
        return "wfrs-" + sha256_json(self.to_dict())

    def parent(self) -> "WorkflowRolloutScope | None":
        if self.workflow_id:
            return replace(self, workflow_id="")
        if self.profile_id:
            return replace(self, profile_id="")
        if self.tenant_id:
            return replace(self, tenant_id="")
        return None

    def lineage(self) -> tuple["WorkflowRolloutScope", ...]:
        result = [WorkflowRolloutScope(self.project_id)]
        if self.tenant_id:
            result.append(WorkflowRolloutScope(self.project_id, self.tenant_id))
        if self.profile_id:
            result.append(WorkflowRolloutScope(self.project_id, self.tenant_id, self.profile_id))
        if self.workflow_id:
            result.append(self)
        return tuple(result)

    def assert_valid(self) -> None:
        if not self.project_id:
            raise ValueError("workflow_rollout_project_scope_required")
        if self.profile_id and not self.tenant_id:
            raise ValueError("workflow_rollout_profile_parent_required")
        if self.workflow_id and not self.profile_id:
            raise ValueError("workflow_rollout_workflow_parent_required")
        if any(len(value) > 160 for value in self.to_dict().values()):
            raise ValueError("workflow_rollout_scope_identifier_too_long")

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "tenant_id": self.tenant_id,
            "profile_id": self.profile_id,
            "workflow_id": self.workflow_id,
        }


class RolloutPlanPolicy(Protocol):
    """Minimal policy surface required for execution-plan admission."""

    scope: WorkflowRolloutScope
    allowed_side_effect_classes: tuple[str, ...]
    allowed_egress_destinations: tuple[str, ...]

    def assert_valid(self) -> None: ...


def rollout_scope_from_plan(plan: ExecutionPlan) -> WorkflowRolloutScope:
    """Return the mandatory scope compiled by the Hub and bind identities."""

    raw = plan.metadata.get("workflow_rollout_scope")
    if not isinstance(raw, Mapping):
        raise ValueError("workflow_rollout_plan_scope_required")
    values = dict(raw)
    declared_tenant = str(values.get("tenant_id") or "").strip()
    declared_workflow = str(values.get("workflow_id") or "").strip()
    if declared_tenant and declared_tenant != plan.tenant_id:
        raise ValueError("workflow_rollout_plan_tenant_mismatch")
    if declared_workflow and declared_workflow != plan.workflow_id:
        raise ValueError("workflow_rollout_plan_workflow_mismatch")
    profile_id = str(values.get("profile_id") or "").strip()
    values["tenant_id"] = plan.tenant_id
    values["profile_id"] = profile_id
    values["workflow_id"] = plan.workflow_id if profile_id else ""
    try:
        return WorkflowRolloutScope.from_mapping(values)
    except ValueError:
        # An explicit malformed scope must never silently widen to defaults.
        raise ValueError("workflow_rollout_plan_scope_invalid") from None


def assert_rollout_policy_allows_plan(
    *,
    policy: RolloutPlanPolicy,
    plan: ExecutionPlan,
    plan_scope: WorkflowRolloutScope | None = None,
) -> None:
    """Enforce scope, side-effect and egress policy before delegation."""

    policy.assert_valid()
    plan.assert_valid()
    resolved_scope = plan_scope or rollout_scope_from_plan(plan)
    if policy.scope not in resolved_scope.lineage():
        raise ValueError("workflow_rollout_plan_scope_mismatch")
    denied_effects = sorted({node.side_effect_class for node in plan.nodes} - set(policy.allowed_side_effect_classes))
    if denied_effects:
        raise PermissionError("workflow_rollout_plan_side_effect_denied:" + ",".join(denied_effects))
    denied_egress = sorted(set(_plan_egress_destinations(plan)) - set(policy.allowed_egress_destinations))
    if denied_egress:
        raise PermissionError("workflow_rollout_plan_egress_denied")


def assert_safe_shadow_to_live_transition(
    shadow: RolloutPlanPolicy,
    live: RolloutPlanPolicy,
) -> None:
    fields = (
        "scope",
        "preferred_runtime",
        "allowed_runtimes",
        "required_capabilities",
        "allowed_side_effect_classes",
        "allowed_egress_destinations",
        "fallback_semantics",
    )
    if any(getattr(shadow, field_name) != getattr(live, field_name) for field_name in fields):
        raise ValueError("workflow_rollout_promotion_policy_drift")


def string_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("workflow_rollout_string_list_required")
    if any(not isinstance(value, str) for value in values):
        raise ValueError("workflow_rollout_string_list_required")
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def runtime_tuple(values: Any) -> tuple[str, ...]:
    return tuple(canonical_runtime_id(value) for value in string_tuple(values))


def _plan_egress_destinations(plan: ExecutionPlan) -> tuple[str, ...]:
    destinations: set[str] = set()
    _collect_egress(destinations, plan.metadata, path="metadata")
    for index, node in enumerate(plan.nodes):
        _collect_egress(destinations, node.metadata, path=f"nodes[{index}].metadata")
    return tuple(sorted(destinations))


def _collect_egress(destinations: set[str], metadata: Mapping[str, Any], *, path: str) -> None:
    singular = metadata.get("egress_destination")
    if singular is not None:
        if not isinstance(singular, str) or not singular.strip():
            raise ValueError(f"workflow_rollout_egress_destination_invalid:{path}")
        destinations.add(singular.strip())
    plural = metadata.get("egress_destinations")
    if plural is None:
        return
    if not isinstance(plural, (list, tuple, set, frozenset)) or any(
        not isinstance(value, str) or not value.strip() for value in plural
    ):
        raise ValueError(f"workflow_rollout_egress_destinations_invalid:{path}")
    destinations.update(value.strip() for value in plural)


__all__ = [
    "RolloutPlanPolicy",
    "WorkflowRolloutScope",
    "assert_rollout_policy_allows_plan",
    "assert_safe_shadow_to_live_transition",
    "canonical_runtime_id",
    "rollout_scope_from_plan",
    "runtime_tuple",
    "string_tuple",
]
