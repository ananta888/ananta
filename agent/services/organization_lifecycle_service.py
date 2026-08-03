"""Pure lifecycle planner for Organization instances."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OrganizationActivitySnapshot:
    running_task_ids: tuple[str, ...] = ()
    active_lease_ids: tuple[str, ...] = ()
    open_gate_ids: tuple[str, ...] = ()
    open_handoff_ids: tuple[str, ...] = ()
    active_assignment_ids: tuple[str, ...] = ()

    @property
    def has_active_work(self) -> bool:
        return any(
            (
                self.running_task_ids,
                self.active_lease_ids,
                self.open_gate_ids,
                self.open_handoff_ids,
            )
        )


@dataclass(frozen=True, slots=True)
class OrganizationLifecyclePlan:
    organization_id: str
    from_state: str
    to_state: str
    allowed: bool
    reason_code: str
    required_operations: tuple[str, ...]
    preserves_lineage: tuple[str, ...]
    starts_workers: bool
    reruns_tasks: bool
    plan_digest: str


class OrganizationLifecycleService:
    """Plans transitions; persistence belongs to an Organization UoW service."""

    _TRANSITIONS = {
        "draft": frozenset({"validated", "archived"}),
        "validated": frozenset({"active", "draft", "archived"}),
        "active": frozenset({"paused", "completed"}),
        "paused": frozenset({"active", "completed", "archived"}),
        "completed": frozenset({"archived"}),
        "archived": frozenset({"validated"}),
    }

    def plan_transition(
        self,
        *,
        organization_id: str,
        current_state: str,
        target_state: str,
        activity: OrganizationActivitySnapshot,
        active_work_strategy: str | None = None,
    ) -> OrganizationLifecyclePlan:
        source = str(current_state or "").lower()
        target = str(target_state or "").lower()
        reason = "organization_lifecycle_transition_allowed"
        operations: list[str] = []
        allowed = True
        if not organization_id or source not in self._TRANSITIONS or target not in self._TRANSITIONS:
            allowed = False
            reason = "organization_lifecycle_state_invalid"
        elif target not in self._TRANSITIONS[source]:
            allowed = False
            reason = "organization_lifecycle_transition_invalid"
        elif source == "active" and target in {"archived", "draft", "validated"}:
            allowed = False
            reason = "active_organization_must_pause_first"
        elif target in {"completed", "archived"} and activity.has_active_work:
            strategy = str(active_work_strategy or "").lower()
            if strategy not in {"drain", "migrate", "cancel"}:
                allowed = False
                reason = "organization_active_work_strategy_required"
            else:
                operations.extend(
                    [
                        f"{strategy}_running_tasks",
                        f"{strategy}_active_leases",
                        "resolve_open_gates",
                        "resolve_open_handoffs",
                    ]
                )
        if target == "archived":
            operations.append("seal_immutable_archive_snapshot")
            operations.append("archive_topology")
        if target == "active":
            operations.append("activate_planned_topology")
        if target == "paused":
            operations.append("pause_dispatch_topology")
        if target == "completed":
            operations.append("close_dispatch_topology")
        if source == "archived" and target == "validated":
            operations.append("create_new_activation_candidate")
            reason = "organization_recovery_requires_new_activation"

        payload = {
            "organization_id": organization_id,
            "from_state": source,
            "to_state": target,
            "allowed": allowed,
            "reason_code": reason,
            "required_operations": operations,
            "activity": {
                "running_task_ids": list(activity.running_task_ids),
                "active_lease_ids": list(activity.active_lease_ids),
                "open_gate_ids": list(activity.open_gate_ids),
                "open_handoff_ids": list(activity.open_handoff_ids),
                "active_assignment_ids": list(activity.active_assignment_ids),
            },
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return OrganizationLifecyclePlan(
            organization_id=organization_id,
            from_state=source,
            to_state=target,
            allowed=allowed,
            reason_code=reason,
            required_operations=tuple(operations),
            preserves_lineage=(
                "definition",
                "snapshots",
                "relations",
                "goals",
                "tasks",
                "handoffs",
                "assignments",
                "artifacts",
                "audit",
            ),
            starts_workers=False,
            reruns_tasks=False,
            plan_digest=digest,
        )


__all__ = [
    "OrganizationActivitySnapshot",
    "OrganizationLifecyclePlan",
    "OrganizationLifecycleService",
]
