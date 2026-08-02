"""Deterministic Organization definition drift and upgrade planning.

The planner is deliberately write-free.  It compares immutable definition
revisions, reports local-override conflicts and projects which currently
linked assignments would be affected.  Applying a plan remains a Hub-side
application-service responsibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

from agent.models.organization_models import canonical_definition_sha256


@dataclass(frozen=True, slots=True)
class OrganizationDriftEntry:
    path: str
    change_kind: str
    current_hash: str | None
    desired_hash: str | None
    conflict: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class OrganizationEntityDrift:
    section: str
    entity_kind: str
    entity_key: str
    change_kind: str
    current_hash: str | None
    desired_hash: str | None
    conflict: bool


@dataclass(frozen=True, slots=True)
class OrganizationAssignmentImpact:
    organization_id: str
    assignment_id: str
    unit_key: str
    group_key: str | None
    role_slot_key: str
    lifecycle: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrganizationReconciliationPlan:
    definition_key: str
    current_revision: str | None
    desired_revision: str
    drift: tuple[OrganizationDriftEntry, ...]
    entity_drift: tuple[OrganizationEntityDrift, ...]
    assignment_impacts: tuple[OrganizationAssignmentImpact, ...]
    planned_writes: tuple[str, ...]
    preserved_local_overrides: tuple[str, ...]
    preserved_snapshot_revisions: tuple[str, ...]
    blockers: tuple[str, ...]
    plan_digest: str

    @property
    def applicable(self) -> bool:
        return not self.blockers


class OrganizationReconciliationService:
    """Compute drift without deleting seeds or mutating instance snapshots."""

    _SECTIONS = (
        "version",
        "description",
        "parameter_schema",
        "standard_composition",
        "units",
        "unit_groups",
        "role_slots",
        "workflows",
        "relations",
        "policies",
        "shared_product_model",
        "orchestration",
        "governance",
        "budgets",
        "limit_policy_ref",
        "referenced_versions",
    )
    _ENTITY_KEYS: dict[str, tuple[str, ...]] = {
        "units": ("unit_key", "id", "key"),
        "unit_groups": ("group_id", "id", "key"),
        "role_slots": ("slot_id", "slot_key", "id", "key"),
        "workflows": ("step_id", "id", "key", "workflow_ref"),
        "relations": ("relation_id", "id", "key"),
        "policies": ("key", "policy_ref", "id"),
        "referenced_versions": ("key", "definition_ref", "id"),
    }

    def plan(
        self,
        *,
        definition_key: str,
        current_definition: Mapping[str, object] | None,
        desired_definition: Mapping[str, object],
        current_revision: str | None = None,
        desired_revision: str | None = None,
        local_override_paths: tuple[str, ...] = (),
        active_instance_snapshot_revisions: tuple[str, ...] = (),
        active_assignment_links: Sequence[Mapping[str, object]] = (),
        removed_from_seed: bool = False,
        removal_lifecycle_approved: bool = False,
    ) -> OrganizationReconciliationPlan:
        current = dict(current_definition or {})
        desired = dict(desired_definition)
        effective_current_revision = (
            current_revision if current_revision is not None else _hash(current) if current else None
        )
        effective_desired_revision = desired_revision or _hash(desired)
        overrides = tuple(sorted(set(local_override_paths)))
        drift: list[OrganizationDriftEntry] = []
        entity_drift: list[OrganizationEntityDrift] = []
        writes: list[str] = []
        blockers: list[str] = []

        if removed_from_seed:
            if not removal_lifecycle_approved:
                blockers.append("seed_removal_requires_explicit_lifecycle")
            else:
                writes.append("archive_seed_definition_revision")

        for section in self._SECTIONS:
            current_value = current.get(section)
            desired_value = desired.get(section)
            if current_value == desired_value:
                continue
            path = f"$.{section}"
            conflict = _path_conflicts(path, overrides)
            change_kind = _change_kind(current_value, desired_value)
            reason = "local_override_conflict" if conflict else f"organization_{section}_drift"
            drift.append(
                OrganizationDriftEntry(
                    path=path,
                    change_kind=change_kind,
                    current_hash=_optional_hash(current_value),
                    desired_hash=_optional_hash(desired_value),
                    conflict=conflict,
                    reason_code=reason,
                )
            )
            entity_drift.extend(
                self._entity_drift(
                    section=section,
                    current_value=current_value,
                    desired_value=desired_value,
                    override_paths=overrides,
                )
            )
            if conflict:
                blockers.append(f"local_override_conflict:{path}")
            else:
                writes.append(f"create_definition_revision:{section}")

        snapshots = tuple(sorted(set(active_instance_snapshot_revisions)))
        if snapshots:
            writes.append("preserve_active_instance_snapshots")
        assignment_impacts = self._assignment_impacts(
            active_assignment_links=active_assignment_links,
            entity_drift=entity_drift,
            changed_sections={entry.path.removeprefix("$.") for entry in drift},
        )
        payload = {
            "definition_key": definition_key,
            "current_revision": effective_current_revision,
            "desired_revision": effective_desired_revision,
            "drift": [asdict(entry) for entry in drift],
            "entity_drift": [asdict(entry) for entry in entity_drift],
            "assignment_impacts": [asdict(entry) for entry in assignment_impacts],
            "planned_writes": sorted(set(writes)),
            "preserved_local_overrides": list(overrides),
            "preserved_snapshot_revisions": list(snapshots),
            "blockers": sorted(set(blockers)),
        }
        return OrganizationReconciliationPlan(
            definition_key=definition_key,
            current_revision=effective_current_revision,
            desired_revision=effective_desired_revision,
            drift=tuple(drift),
            entity_drift=tuple(entity_drift),
            assignment_impacts=assignment_impacts,
            planned_writes=tuple(payload["planned_writes"]),
            preserved_local_overrides=overrides,
            preserved_snapshot_revisions=snapshots,
            blockers=tuple(payload["blockers"]),
            plan_digest=_hash(payload),
        )

    def _entity_drift(
        self,
        *,
        section: str,
        current_value: object,
        desired_value: object,
        override_paths: tuple[str, ...],
    ) -> list[OrganizationEntityDrift]:
        if section not in self._ENTITY_KEYS:
            return []
        current = _entity_map(current_value, key_fields=self._ENTITY_KEYS[section])
        desired = _entity_map(desired_value, key_fields=self._ENTITY_KEYS[section])
        result: list[OrganizationEntityDrift] = []
        for key in sorted(set(current) | set(desired)):
            before = current.get(key)
            after = desired.get(key)
            if before == after:
                continue
            path = f"$.{section}.{key}"
            result.append(
                OrganizationEntityDrift(
                    section=section,
                    entity_kind=_entity_kind(section),
                    entity_key=key,
                    change_kind=_change_kind(before, after),
                    current_hash=_optional_hash(before),
                    desired_hash=_optional_hash(after),
                    conflict=_path_conflicts(path, override_paths) or _path_conflicts(f"$.{section}", override_paths),
                )
            )
        return result

    @staticmethod
    def _assignment_impacts(
        *,
        active_assignment_links: Sequence[Mapping[str, object]],
        entity_drift: Sequence[OrganizationEntityDrift],
        changed_sections: set[str],
    ) -> tuple[OrganizationAssignmentImpact, ...]:
        changed_units = {entry.entity_key for entry in entity_drift if entry.section == "units"}
        changed_groups = {entry.entity_key for entry in entity_drift if entry.section == "unit_groups"}
        changed_slots = {entry.entity_key for entry in entity_drift if entry.section == "role_slots"}
        global_sections = {
            "workflows",
            "policies",
            "governance",
            "orchestration",
            "shared_product_model",
            "budgets",
            "limit_policy_ref",
            "referenced_versions",
        }
        global_change = bool(changed_sections & global_sections)
        relation_change = "relations" in changed_sections
        impacts: list[OrganizationAssignmentImpact] = []
        for raw in active_assignment_links:
            unit_key = str(raw.get("unit_key") or "")
            group_key = str(raw.get("group_key") or "") or None
            slot_key = str(raw.get("role_slot_key") or raw.get("slot_key") or "")
            role_definition_key = str(raw.get("role_definition_key") or slot_key)
            reasons: list[str] = []
            if unit_key in changed_units:
                reasons.append("unit_definition_changed")
            if group_key and group_key in changed_groups:
                reasons.append("unit_group_definition_changed")
            if role_definition_key in changed_slots or slot_key in changed_slots:
                reasons.append("role_slot_definition_changed")
            if global_change:
                reasons.append("policy_workflow_or_reference_changed")
            if relation_change:
                reasons.append("relation_definition_changed")
            if not reasons:
                continue
            impacts.append(
                OrganizationAssignmentImpact(
                    organization_id=str(raw.get("organization_id") or ""),
                    assignment_id=str(raw.get("assignment_id") or ""),
                    unit_key=unit_key,
                    group_key=group_key,
                    role_slot_key=slot_key,
                    lifecycle=str(raw.get("lifecycle") or "active"),
                    reasons=tuple(sorted(set(reasons))),
                )
            )
        return tuple(
            sorted(
                impacts,
                key=lambda value: (
                    value.organization_id,
                    value.unit_key,
                    value.role_slot_key,
                    value.assignment_id,
                ),
            )
        )


def _path_conflicts(path: str, overrides: Sequence[str]) -> bool:
    return any(_is_same_or_child(path, override) or _is_same_or_child(override, path) for override in overrides)


def _is_same_or_child(value: str, parent: str) -> bool:
    return value == parent or value.startswith(f"{parent}.") or value.startswith(f"{parent}[")


def _change_kind(current: object, desired: object) -> str:
    return "add" if current is None else "remove" if desired is None else "change"


def _optional_hash(value: object) -> str | None:
    return _hash(value) if value is not None else None


def _entity_map(value: object, *, key_fields: tuple[str, ...]) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return {}
    result: dict[str, object] = {}
    for index, item in enumerate(value):
        key = ""
        if isinstance(item, Mapping):
            for field in key_fields:
                candidate = str(item.get(field) or "").strip()
                if candidate:
                    key = candidate
                    break
        elif isinstance(item, str):
            key = item
        if not key:
            key = f"item-{index:04d}-{_hash(item)[:12]}"
        result[key] = item
    return result


def _entity_kind(section: str) -> str:
    return {
        "units": "unit",
        "unit_groups": "unit_group",
        "role_slots": "role_slot",
        "workflows": "workflow",
        "relations": "relation",
        "policies": "policy",
        "referenced_versions": "definition_reference",
    }[section]


def _hash(value: object) -> str:
    return canonical_definition_sha256(value)


__all__ = [
    "OrganizationAssignmentImpact",
    "OrganizationDriftEntry",
    "OrganizationEntityDrift",
    "OrganizationReconciliationPlan",
    "OrganizationReconciliationService",
]
