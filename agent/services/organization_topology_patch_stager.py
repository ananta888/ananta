"""Stages an evaluated topology patch and its revision snapshot inside the caller's unit of work."""

from __future__ import annotations

from collections.abc import Callable

from agent.db_models.organizations import (
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.teams import TeamDB
from agent.models.organization_models import (
    VersionedDefinitionRef,
    canonical_sha256,
)
from agent.services.organization_active_work_service import (
    OrganizationActiveWorkError,
    SqlOrganizationActiveWorkService,
)
from agent.services.organization_topology_patch_contracts import (
    OrganizationTopologyPatchError,
    TopologyAddOperation,
    TopologyConnectOperation,
    TopologyRemoveOperation,
    TopologyReparentOperation,
)
from agent.services.organization_topology_patch_graph import (
    planned_node_id,
    unit_subtree_ids,
)


class OrganizationTopologyPatchStager:
    """Write normalized topology rows for already evaluated patch operations."""

    def __init__(
        self,
        *,
        active_work: SqlOrganizationActiveWorkService,
        clock: Callable[[], float],
        fault_injector: Callable[[str], None],
    ) -> None:
        self._active_work = active_work
        self._clock = clock
        self._fault_injector = fault_injector

    def stage_operations(self, uow, state, operations, *, operation_key: str, principal_id: str):
        units = {row.id: row for row in state.units}
        slots = {row.id: row for row in state.role_slots}
        assignments = {row.id: row for row in state.assignments}
        relations = {row.id: row for row in state.relations}
        links_by_unit = {row.unit_id: row for row in state.team_links}
        for index, operation in enumerate(operations):
            if isinstance(operation, TopologyAddOperation):
                planned_id = planned_node_id(
                    state.organization.organization_id, operation.node_kind, operation.value.stable_key
                )
                if operation.node_kind == "role_slot":
                    role_ref = VersionedDefinitionRef.parse(str(operation.value.role_template_ref))
                    row = OrganizationRoleSlotDB(
                        id=planned_id,
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        unit_id=operation.parent_id,
                        slot_key=str(operation.value.slot_key),
                        role_template_key=role_ref.key,
                        role_template_version=role_ref.version,
                        required=bool(operation.value.required),
                        min_count=int(operation.value.min_count or 0),
                        default_count=int(operation.value.default_count or 0),
                        max_count=operation.value.max_count,
                        assignment_policy=operation.value.assignment_policy.model_dump(mode="json"),
                        separation_of_duties=operation.value.separation_of_duties.model_dump(mode="json"),
                        overlays=operation.value.overlays,
                    )
                    uow.role_slots.add(row)
                    slots[row.id] = row
                else:
                    team_ref = (
                        VersionedDefinitionRef.parse(operation.value.team_blueprint_ref)
                        if operation.value.team_blueprint_ref
                        else None
                    )
                    row = OrganizationUnitDB(
                        id=planned_id,
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        unit_key=operation.value.stable_key,
                        name=operation.value.name,
                        unit_kind=operation.node_kind,
                        parent_unit_id=operation.parent_id,
                        team_blueprint_key=team_ref.key if team_ref else None,
                        team_blueprint_version=team_ref.version if team_ref else None,
                        lifecycle="planned",
                    )
                    uow.units.add(row)
                    units[row.id] = row
                    if team_ref:
                        portable_ref = team_ref.portable_ref()
                        blueprint_row = state.team_blueprint_rows[portable_ref]
                        team_id = planned_node_id(
                            state.organization.organization_id, "team", operation.value.stable_key
                        )
                        uow.teams.add(
                            TeamDB(
                                id=team_id,
                                name=f"{state.organization.name} / {operation.value.name}",
                                description=f"Organization-managed team {operation.value.stable_key}",
                                blueprint_id=blueprint_row.legacy_blueprint_id,
                                is_active=False,
                                blueprint_snapshot={
                                    "definition_ref": portable_ref,
                                    "definition_hash": blueprint_row.content_hash,
                                },
                            )
                        )
                        link = OrganizationTeamLinkDB(
                            tenant_id=state.organization.tenant_id,
                            project_id=state.organization.project_id,
                            organization_id=state.organization.organization_id,
                            unit_id=row.id,
                            team_id=team_id,
                            lifecycle="planned",
                        )
                        uow.team_links.add(link)
                        links_by_unit[row.id] = link
                        for definition in state.team_blueprints[portable_ref].role_slots:
                            role_ref = VersionedDefinitionRef.parse(definition.role_template_ref)
                            slot_id = planned_node_id(
                                state.organization.organization_id,
                                "role_slot",
                                f"{operation.value.stable_key}:{definition.slot_id}",
                            )
                            slot_row = OrganizationRoleSlotDB(
                                id=slot_id,
                                tenant_id=state.organization.tenant_id,
                                project_id=state.organization.project_id,
                                organization_id=state.organization.organization_id,
                                unit_id=row.id,
                                slot_key=definition.slot_id,
                                role_template_key=role_ref.key,
                                role_template_version=role_ref.version,
                                required=definition.required,
                                min_count=definition.min_count,
                                default_count=definition.default_count,
                                max_count=definition.max_count,
                                assignment_policy=definition.assignment_policy.model_dump(mode="json"),
                                separation_of_duties=definition.separation_of_duties.model_dump(mode="json"),
                                overlays=definition.overlays,
                            )
                            uow.role_slots.add(slot_row)
                            slots[slot_row.id] = slot_row
            elif isinstance(operation, TopologyRemoveOperation):
                if operation.node_id in units:
                    unit_view = {
                        row_id: {"parent_id": row.parent_unit_id}
                        for row_id, row in units.items()
                        if row.lifecycle != "archived"
                    }
                    subtree = unit_subtree_ids(unit_view, operation.node_id)
                    active = any(
                        int(value) > 0
                        for unit_id in subtree
                        for value in state.activity_by_unit.get(unit_id, {}).values()
                    )
                    if active:
                        if operation.lifecycle_strategy == "archive":
                            raise OrganizationTopologyPatchError("organization_active_work_strategy_required")
                        try:
                            self._active_work.execute(
                                session=uow.session,
                                tenant_id=state.organization.tenant_id,
                                project_id=state.organization.project_id,
                                organization_id=state.organization.organization_id,
                                strategy=operation.lifecycle_strategy,
                                operation_key=f"{operation_key}:{index}",
                                principal_id=principal_id,
                                migration_target=(
                                    operation.migration_target.model_dump(mode="json")
                                    if operation.migration_target is not None
                                    else None
                                ),
                                unit_ids=tuple(sorted(subtree)),
                                include_queued=True,
                                now=self._clock(),
                            )
                        except OrganizationActiveWorkError as exc:
                            raise OrganizationTopologyPatchError(exc.reason_code, public_status=422) from exc
                    for assignment in assignments.values():
                        if (
                            assignment.lifecycle == "active"
                            and slots.get(assignment.role_slot_id) is not None
                            and slots[assignment.role_slot_id].unit_id in subtree
                        ):
                            assignment.lifecycle = "ended"
                            assignment.ended_at = self._clock()
                            uow.assignments.add(assignment)
                    lifecycle = "archived"
                    for unit_id in subtree:
                        row = units[unit_id]
                        row.lifecycle = lifecycle
                        uow.units.add(row)
                        if row.id in links_by_unit:
                            links_by_unit[row.id].lifecycle = lifecycle
                            uow.team_links.add(links_by_unit[row.id])
                    for relation in relations.values():
                        if lifecycle == "archived" and subtree & {
                            relation.source_unit_id,
                            relation.target_unit_id,
                        }:
                            relation.lifecycle = "archived"
                            uow.relations.add(relation)
                elif operation.node_id in slots:
                    has_active = any(
                        assignment.role_slot_id == operation.node_id and assignment.lifecycle == "active"
                        for assignment in assignments.values()
                    )
                    if has_active and operation.lifecycle_strategy == "archive":
                        raise OrganizationTopologyPatchError("organization_role_slot_drain_required")
                    if has_active:
                        for assignment in assignments.values():
                            if assignment.role_slot_id == operation.node_id and assignment.lifecycle == "active":
                                assignment.lifecycle = "ended"
                                assignment.ended_at = self._clock()
                                uow.assignments.add(assignment)
                    slots[operation.node_id].lifecycle = "archived"
                    uow.role_slots.add(slots[operation.node_id])
                elif operation.node_id in assignments:
                    assignments[operation.node_id].lifecycle = "ended"
                    assignments[operation.node_id].ended_at = self._clock()
                    uow.assignments.add(assignments[operation.node_id])
                elif operation.node_id in relations:
                    relations[operation.node_id].lifecycle = "archived"
                    uow.relations.add(relations[operation.node_id])
            elif isinstance(operation, TopologyReparentOperation):
                unit_view = {
                    row_id: {"parent_id": row.parent_unit_id}
                    for row_id, row in units.items()
                    if row.lifecycle != "archived"
                }
                subtree = unit_subtree_ids(unit_view, operation.node_id)
                active = any(
                    int(value) > 0 for unit_id in subtree for value in state.activity_by_unit.get(unit_id, {}).values()
                )
                if active:
                    if operation.lifecycle_strategy is None:
                        raise OrganizationTopologyPatchError("organization_active_work_strategy_required")
                    try:
                        self._active_work.execute(
                            session=uow.session,
                            tenant_id=state.organization.tenant_id,
                            project_id=state.organization.project_id,
                            organization_id=state.organization.organization_id,
                            strategy=operation.lifecycle_strategy,
                            operation_key=f"{operation_key}:{index}",
                            principal_id=principal_id,
                            unit_ids=tuple(sorted(subtree)),
                            allow_in_place_migration=operation.lifecycle_strategy == "migrate",
                            include_queued=True,
                            now=self._clock(),
                        )
                    except OrganizationActiveWorkError as exc:
                        raise OrganizationTopologyPatchError(exc.reason_code, public_status=422) from exc
                units[operation.node_id].parent_unit_id = operation.parent_id
                units[operation.node_id].updated_at = self._clock()
                uow.units.add(units[operation.node_id])
            elif isinstance(operation, TopologyConnectOperation):
                relation_key = operation.relation_key or (
                    f"{operation.edge_kind}:{units[operation.source_id].unit_key}:{units[operation.target_id].unit_key}"
                )
                handoff_ref = (
                    VersionedDefinitionRef.parse(operation.handoff_contract_ref)
                    if operation.handoff_contract_ref
                    else None
                )
                row = OrganizationRelationDB(
                    id=planned_node_id(state.organization.organization_id, "relation", relation_key),
                    tenant_id=state.organization.tenant_id,
                    project_id=state.organization.project_id,
                    organization_id=state.organization.organization_id,
                    relation_key=relation_key,
                    namespace="organization",
                    kind=operation.edge_kind,
                    source_unit_id=operation.source_id,
                    target_unit_id=operation.target_id,
                    handoff_definition_key=handoff_ref.key if handoff_ref else None,
                    handoff_definition_version=handoff_ref.version if handoff_ref else None,
                    dependency_policy=operation.dependency_policy,
                    escalation_policy=operation.escalation_policy,
                )
                uow.relations.add(row)
                relations[row.id] = row
            else:
                row = next(
                    (
                        value
                        for value in assignments.values()
                        if value.role_slot_id == operation.role_slot_id and value.agent_url == operation.agent_id
                    ),
                    None,
                )
                if row is None:
                    row = OrganizationRoleAssignmentDB(
                        id=planned_node_id(
                            state.organization.organization_id,
                            "assignment",
                            f"{operation.role_slot_id}:{operation.agent_id}",
                        ),
                        tenant_id=state.organization.tenant_id,
                        project_id=state.organization.project_id,
                        organization_id=state.organization.organization_id,
                        role_slot_id=operation.role_slot_id,
                        agent_url=operation.agent_id,
                    )
                # ``agent_id`` has already been resolved against the Hub's
                # validated Agent registry during preview and revalidation.
                # Persist that canonical identity so later routing and
                # separation-of-duties decisions never infer a principal from
                # a display label or request-body claim.
                metadata = dict(row.assignment_metadata or {})
                metadata.update(
                    {
                        "principal_kind": "registered_worker",
                        "principal_id": operation.agent_id,
                    }
                )
                row.assignment_metadata = metadata
                row.lifecycle = "active"
                row.ended_at = None
                row.assigned_at = self._clock()
                uow.assignments.add(row)
                assignments[row.id] = row
            self._fault_injector(f"operation:{index}")

    def stage_snapshot(self, uow, state, preview):
        units = uow.units.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        links = uow.team_links.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        slots = uow.role_slots.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        assignments = uow.assignments.list_for_organization(
            preview.tenant_id, preview.project_id, preview.organization_id
        )
        relations = uow.relations.list_for_organization(preview.tenant_id, preview.project_id, preview.organization_id)
        unit_by_id = {row.id: row for row in units}
        link_by_unit = {row.unit_id: row for row in links}
        payload = {
            "organization_id": preview.organization_id,
            "definition_revision": state.organization.definition_revision,
            "parent_snapshot_hash": preview.source_snapshot_hash,
            "local_patch_digest": preview.patch_digest,
            "units": [
                {
                    "id": row.id,
                    "unit_key": row.unit_key,
                    "name": row.name,
                    "unit_kind": row.unit_kind,
                    "parent_unit_key": unit_by_id[row.parent_unit_id].unit_key
                    if row.parent_unit_id in unit_by_id
                    else None,
                    "team_id": link_by_unit[row.id].team_id if row.id in link_by_unit else None,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(units, key=lambda value: value.unit_key)
            ],
            "role_slots": [
                {
                    "id": row.id,
                    "unit_id": row.unit_id,
                    "slot_key": row.slot_key,
                    "role_template_ref": f"{row.role_template_key}@{row.role_template_version}",
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(slots, key=lambda value: (value.unit_id, value.slot_key))
            ],
            "assignments": [
                {
                    "id": row.id,
                    "role_slot_id": row.role_slot_id,
                    "agent_url": row.agent_url,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(assignments, key=lambda value: value.id)
            ],
            "relations": [
                {
                    "id": row.id,
                    "relation_key": row.relation_key,
                    "kind": row.kind,
                    "source_unit_id": row.source_unit_id,
                    "target_unit_id": row.target_unit_id,
                    "lifecycle": row.lifecycle,
                }
                for row in sorted(relations, key=lambda value: value.relation_key)
            ],
        }
        if state.snapshot and (state.snapshot.snapshot_json or {}).get("compiled_plan"):
            payload["compiled_plan"] = state.snapshot.snapshot_json["compiled_plan"]
        snapshot_hash = canonical_sha256(payload)
        revision = int(state.snapshot.revision if state.snapshot else 0) + 1
        uow.snapshots.add(
            OrganizationTopologySnapshotDB(
                tenant_id=preview.tenant_id,
                project_id=preview.project_id,
                organization_id=preview.organization_id,
                revision=revision,
                definition_revision=state.organization.definition_revision,
                snapshot_hash=snapshot_hash,
                snapshot_json=payload,
            )
        )
        return snapshot_hash


__all__ = [
    "OrganizationTopologyPatchStager",
]
