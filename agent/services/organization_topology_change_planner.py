"""Write-free resize/reparent planner for organization instances."""

from __future__ import annotations

from agent.models.organization_models import (
    CompiledOrganizationPlan,
    OrganizationDiagnostic,
    OrganizationTopologyChange,
    OrganizationTopologyChangePlan,
    canonical_sha256,
)
from agent.ports.organization_definitions import OrganizationRuntimeGuardPort

ACTIVE_GUARD_KEYS = ("tasks", "leases", "open_gates", "handoffs", "assignments")


class OrganizationTopologyChangePlanner:
    def __init__(self, *, runtime_guard: OrganizationRuntimeGuardPort) -> None:
        self._runtime_guard = runtime_guard

    def plan(
        self,
        *,
        current_snapshot: dict,
        target: CompiledOrganizationPlan,
    ) -> OrganizationTopologyChangePlan:
        if current_snapshot.get("organization_id") != target.organization_id:
            raise ValueError("organization_snapshot_scope_mismatch")
        source_snapshot_hash = str(current_snapshot.get("snapshot_hash") or "")
        if not source_snapshot_hash:
            raise ValueError("organization_snapshot_hash_required")

        current_units = {
            str(item["unit_key"]): item
            for item in current_snapshot.get("units", [])
            if isinstance(item, dict) and item.get("unit_key")
        }
        target_units = {item.unit_key: item for item in target.units}
        changing_keys = sorted(
            key
            for key in current_units
            if key not in target_units or current_units[key].get("parent_unit_key") != target_units[key].parent_unit_key
        )
        activity_by_unit = self._runtime_guard.unit_activity(target.organization_id, changing_keys)
        warnings: list[OrganizationDiagnostic] = []
        blockers: list[OrganizationDiagnostic] = []
        changes: list[OrganizationTopologyChange] = []

        for key in sorted(set(current_units) | set(target_units)):
            current = current_units.get(key)
            desired = target_units.get(key)
            activity = {name: int((activity_by_unit.get(key) or {}).get(name, 0)) for name in ACTIVE_GUARD_KEYS}
            is_active = any(activity.values())
            if current is None:
                action = "create"
            elif desired is None:
                action = "drain" if is_active else "archive"
            elif current.get("parent_unit_key") != desired.parent_unit_key:
                action = "drain" if is_active else "reparent"
            else:
                action = "retain"
            requires_confirmation = action in {"drain", "archive", "reparent"}
            changes.append(
                OrganizationTopologyChange(
                    action=action,
                    unit_key=key,
                    current_parent_unit_key=current.get("parent_unit_key") if current else None,
                    target_parent_unit_key=desired.parent_unit_key if desired else None,
                    activity=activity,
                    requires_confirmation=requires_confirmation,
                )
            )
            if action == "drain":
                blockers.append(
                    OrganizationDiagnostic(
                        path=f"$.units[{key}]",
                        reason_code="ORGANIZATION_UNIT_DRAIN_REQUIRED",
                        human_message="Active runtime bindings must be drained or migrated before topology mutation.",
                        severity="blocker",
                        details={"activity": activity},
                    )
                )
            elif requires_confirmation:
                warnings.append(
                    OrganizationDiagnostic(
                        path=f"$.units[{key}]",
                        reason_code="ORGANIZATION_TOPOLOGY_CONFIRMATION_REQUIRED",
                        human_message="Destructive topology changes require an explicit Hub confirmation.",
                        severity="warning",
                    )
                )

        payload = {
            "organization_id": target.organization_id,
            "source_snapshot_hash": source_snapshot_hash,
            "target_plan_digest": target.plan_digest,
            "target_team_count": target.requested_team_count,
            "changes": [item.model_dump(mode="json") for item in changes],
            "warnings": [item.model_dump(mode="json") for item in warnings],
            "blockers": [item.model_dump(mode="json") for item in blockers],
        }
        return OrganizationTopologyChangePlan(
            **payload,
            change_plan_digest=canonical_sha256(payload),
        )


__all__ = ["ACTIVE_GUARD_KEYS", "OrganizationTopologyChangePlanner"]
