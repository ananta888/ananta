"""Pure projections of Organization blueprint definitions for revision binding and reconciliation.

The functions resolve referenced definition hashes, enrich a definition for
role/workflow/policy drift detection and shape reconciliation plans for the API.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    VersionedDefinitionRef,
)
from agent.services.organization_definition_errors import OrganizationDefinitionMutationError
from agent.services.organization_reconciliation_service import (
    OrganizationReconciliationPlan,
)


def definition_reference_hashes(definition, *, definitions) -> dict[str, str]:
    refs: set[str] = {definition.limit_policy_ref, definition.budgets.policy_ref}
    team_refs = {value for value in (unit.team_blueprint_ref for unit in definition.units) if value}
    team_refs.update(group.team_blueprint_ref for group in definition.unit_groups)
    refs.update(team_refs)
    refs.update(group.limit_policy_ref for group in definition.unit_groups)
    refs.update(value for value in (relation.handoff_contract_ref for relation in definition.relations) if value)
    for value in sorted(team_refs):
        team_ref = VersionedDefinitionRef.parse(value)
        team = definitions.get_team_blueprint(team_ref.key, team_ref.version)
        if team is None:
            continue
        refs.add(team.workflow_ref)
        refs.update(team.policies)
        for slot in team.role_slots:
            refs.add(slot.role_template_ref)
            refs.update(slot.overlays)
    result: dict[str, str] = {}
    for value in sorted(refs):
        content_hash = definitions.content_hash_for_ref(value)
        if content_hash is None:
            raise OrganizationDefinitionMutationError("organization_referenced_definition_hash_missing")
        result[value] = content_hash
    return result


def reconciliation_projection(
    definition: OrganizationBlueprintDefinition,
    *,
    definitions,
    reference_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Enrich an immutable definition for role/workflow/policy drift only."""

    payload = definition.model_dump(mode="json")
    team_refs = {
        value
        for value in (
            *(unit.team_blueprint_ref for unit in definition.units),
            *(group.team_blueprint_ref for group in definition.unit_groups),
        )
        if value
    }
    role_slots: list[dict[str, Any]] = []
    workflows: dict[str, dict[str, Any]] = {}
    policy_refs = {definition.limit_policy_ref, definition.budgets.policy_ref}
    policy_refs.update(group.limit_policy_ref for group in definition.unit_groups)
    for team_ref in sorted(team_refs):
        key, _separator, raw_version = team_ref.rpartition("@")
        team = definitions.get_team_blueprint(key, int(raw_version))
        if team is None:
            continue
        for slot in team.role_slots:
            item = slot.model_dump(mode="json")
            item["slot_id"] = f"{team_ref}:{slot.slot_id}"
            item["team_blueprint_ref"] = team_ref
            role_slots.append(item)
            policy_refs.update(slot.overlays)
        workflow_key, _separator, workflow_version = team.workflow_ref.rpartition("@")
        workflow = definitions.get_workflow_definition(
            workflow_key,
            int(workflow_version),
        )
        workflows[team.workflow_ref] = {
            "key": team.workflow_ref,
            "definition": workflow or {"unresolved": True},
        }
        policy_refs.update(team.policies)
    payload["role_slots"] = role_slots
    payload["workflows"] = [workflows[key] for key in sorted(workflows)]
    payload["policies"] = [
        {
            "key": value,
            "content_hash": definitions.content_hash_for_ref(value),
        }
        for value in sorted(policy_refs)
    ]
    payload["referenced_versions"] = dict(sorted(reference_hashes.items()))
    return payload


def normalized_override_paths(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        path = str(value or "").strip()
        if not path.startswith("$.") or len(path) > 512 or any(character.isspace() for character in path):
            raise OrganizationDefinitionMutationError("organization_local_override_path_invalid")
        normalized.append(path)
    return tuple(sorted(set(normalized)))


def reconciliation_plan_payload(plan: OrganizationReconciliationPlan) -> dict[str, Any]:
    return {
        "definition_key": plan.definition_key,
        "current_revision": plan.current_revision,
        "desired_revision": plan.desired_revision,
        "drift": [asdict(value) for value in plan.drift],
        "entity_drift": [asdict(value) for value in plan.entity_drift],
        "assignment_impacts": [asdict(value) for value in plan.assignment_impacts],
        "planned_writes": list(plan.planned_writes),
        "preserved_local_overrides": list(plan.preserved_local_overrides),
        "preserved_snapshot_revisions": list(plan.preserved_snapshot_revisions),
        "blockers": list(plan.blockers),
        "plan_digest": plan.plan_digest,
        "applicable": plan.applicable,
        "requires_apply": bool(plan.drift),
    }


__all__ = [
    "definition_reference_hashes",
    "normalized_override_paths",
    "reconciliation_plan_payload",
    "reconciliation_projection",
]
