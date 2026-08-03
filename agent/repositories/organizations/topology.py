"""Batch SQL adapter for hierarchy and graph projections."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlmodel import Session, select

from agent.common.agent_endpoint import safe_agent_endpoint_for_display
from agent.db_models.blueprints import ArtifactDB, ArtifactVersionDB
from agent.db_models.organizations import (
    CrossTeamTaskDependencyDB,
    OrganizationInstanceDB,
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationTopologySnapshotDB,
    OrganizationUnitDB,
)
from agent.db_models.tasks import TaskDB

_ACTIVE_TASK_STATES = frozenset(
    {
        "assigned",
        "delegated",
        "doing",
        "in_progress",
        "running",
        "verifying",
    }
)
_BLOCKED_TASK_STATES = frozenset({"blocked", "awaiting_approval", "needs_human_review"})
_FAILED_TASK_STATES = frozenset({"error", "failed"})
_PENDING_TASK_STATES = frozenset({"created", "pending", "planned", "proposed", "queued", "ready", "todo"})
_COMPLETED_TASK_STATES = frozenset({"completed", "done", "passed", "succeeded", "verified"})
_CANCELLED_TASK_STATES = frozenset({"cancelled", "canceled"})
_RUNTIME_ARTIFACTS_PER_NODE = 5


class SqlOrganizationTopologyReadRepository:
    """Loads each entity type in one statement, never once per node.

    ``limit`` bounds the page of structural units.  Role slots and
    assignments are loaded only for that bounded parent page; the projection
    service applies the aggregate render limits to the resulting node count.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def load_topology_snapshot(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        include_runtime_overlay: bool,
        cursor: str | None,
        limit: int,
        max_depth: int,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        organization = self._session.exec(
            select(OrganizationInstanceDB)
            .where(OrganizationInstanceDB.tenant_id == tenant_id)
            .where(OrganizationInstanceDB.project_id == project_id)
            .where(OrganizationInstanceDB.organization_id == organization_id)
        ).first()
        if organization is None:
            return {}

        # One bounded aggregate query keeps parent identities available across
        # cursor pages.  Paging only a SQL slice first would incorrectly attach
        # children whose parent lived on a previous page to the organization.
        unit_statement = (
            select(OrganizationUnitDB)
            .where(OrganizationUnitDB.tenant_id == tenant_id)
            .where(OrganizationUnitDB.project_id == project_id)
            .where(OrganizationUnitDB.organization_id == organization_id)
            .where(OrganizationUnitDB.lifecycle != "archived")
            .order_by(OrganizationUnitDB.unit_key)
        )
        all_units = list(self._session.exec(unit_statement).all())
        all_unit_by_id = {unit.id: unit for unit in all_units}
        children_by_parent: dict[str, list[OrganizationUnitDB]] = defaultdict(list)
        for unit in all_units:
            if unit.parent_unit_id:
                children_by_parent[unit.parent_unit_id].append(unit)

        subgraph_root = str(filters.get("subgraph_root_id") or "").strip()
        root_unit = (
            next(
                (unit for unit in all_units if unit.id == subgraph_root or unit.unit_key == subgraph_root),
                None,
            )
            if subgraph_root
            else None
        )
        if subgraph_root and root_unit is None and subgraph_root != organization_id:
            return {
                "organization_id": organization.organization_id,
                "name": organization.name,
                "definition_revision": organization.definition_revision,
                "snapshot_hash": organization.plan_digest,
                "units": [],
                "role_slots": [],
                "assignments": [],
                "relations": [],
                "runtime_edges": [],
                "diagnostics": [
                    {
                        "severity": "blocker",
                        "reason_code": "ORGANIZATION_SUBGRAPH_ROOT_NOT_FOUND",
                        "message": "Requested subgraph root is outside the organization topology.",
                    }
                ],
                "next_cursor": None,
            }

        candidates = [
            unit
            for unit in all_units
            if _within_requested_depth(
                unit,
                all_unit_by_id,
                root_unit=root_unit,
                max_depth=max_depth,
            )
        ]
        requested_kinds = {str(value).strip() for value in filters.get("kinds") or [] if str(value).strip()}
        requested_unit_kinds = requested_kinds & {
            "coordination_unit",
            "value_stream",
            "team",
        }
        include_role_slots = not requested_kinds or "role_slot" in requested_kinds
        include_assignments = not requested_kinds or "assignment" in requested_kinds
        include_role_descendants = include_role_slots or include_assignments

        # Explicit node-kind filtering is exact.  Team rows remain internal
        # paging anchors when only role slots/assignments are requested, but
        # are not exposed unless ``team`` itself was selected.  The legacy
        # unit_kind filter keeps its former nested-child behavior.
        if requested_kinds:
            candidates = [
                unit
                for unit in candidates
                if unit.unit_kind in requested_unit_kinds or (include_role_descendants and unit.unit_kind == "team")
            ]
        elif filters.get("unit_kind"):
            legacy_unit_kind = str(filters["unit_kind"]).strip()
            candidates = [unit for unit in candidates if unit.unit_kind == legacy_unit_kind]
        search = str(filters.get("search") or "").strip().casefold()
        if search:
            candidates = [
                unit for unit in candidates if search in unit.name.casefold() or search in unit.unit_key.casefold()
            ]
        if cursor:
            candidates = [unit for unit in candidates if unit.unit_key > cursor]
        candidates.sort(key=lambda unit: unit.unit_key)
        has_more = len(candidates) > limit
        page_units = candidates[:limit]
        page_unit_ids = [unit.id for unit in page_units]
        visible_units = [unit for unit in page_units if not requested_kinds or unit.unit_kind in requested_unit_kinds]
        visible_unit_ids = [unit.id for unit in visible_units]

        linked_unit_ids = page_unit_ids if include_runtime_overlay else visible_unit_ids
        links = self._list_for_ids(
            OrganizationTeamLinkDB,
            OrganizationTeamLinkDB.unit_id,
            linked_unit_ids,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        link_by_unit = {link.unit_id: link for link in links}
        parent_slots = (
            self._list_for_ids(
                OrganizationRoleSlotDB,
                OrganizationRoleSlotDB.unit_id,
                page_unit_ids,
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            if include_role_descendants or include_runtime_overlay
            else []
        )
        parent_slots.sort(key=lambda slot: (slot.unit_id, slot.slot_key, slot.id))
        slots = parent_slots if include_role_slots else []
        parent_assignments = (
            self._list_for_ids(
                OrganizationRoleAssignmentDB,
                OrganizationRoleAssignmentDB.role_slot_id,
                [slot.id for slot in parent_slots],
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
            )
            if include_assignments or include_runtime_overlay
            else []
        )
        assignments = parent_assignments if include_assignments else []
        assignments.sort(key=lambda assignment: (assignment.role_slot_id, assignment.id))

        relation_statement = (
            select(OrganizationRelationDB)
            .where(OrganizationRelationDB.tenant_id == tenant_id)
            .where(OrganizationRelationDB.project_id == project_id)
            .where(OrganizationRelationDB.organization_id == organization_id)
            .where(OrganizationRelationDB.lifecycle == "active")
        )
        relation_unit_ids = page_unit_ids if include_runtime_overlay else visible_unit_ids
        if relation_unit_ids:
            relation_statement = relation_statement.where(
                OrganizationRelationDB.source_unit_id.in_(relation_unit_ids),
                OrganizationRelationDB.target_unit_id.in_(relation_unit_ids),
            )
            page_relations = list(self._session.exec(relation_statement).all())
        else:
            page_relations = []
        relations = [
            relation
            for relation in page_relations
            if relation.source_unit_id in visible_unit_ids and relation.target_unit_id in visible_unit_ids
        ]

        latest_snapshot = self._session.exec(
            select(OrganizationTopologySnapshotDB)
            .where(OrganizationTopologySnapshotDB.tenant_id == tenant_id)
            .where(OrganizationTopologySnapshotDB.project_id == project_id)
            .where(OrganizationTopologySnapshotDB.organization_id == organization_id)
            .order_by(OrganizationTopologySnapshotDB.revision.desc())
        ).first()

        unit_by_id = {unit.id: unit for unit in visible_units}
        runtime_definition_revision = (
            latest_snapshot.definition_revision if latest_snapshot is not None else organization.definition_revision
        )
        runtime_snapshot_hash = (
            latest_snapshot.snapshot_hash if latest_snapshot is not None else organization.plan_digest
        )
        runtime_nodes: list[dict[str, Any]] = []
        runtime_edges: list[dict[str, Any]] = []
        if include_runtime_overlay:
            runtime_nodes, runtime_edges = self._runtime_projection(
                tenant_id=tenant_id,
                project_id=project_id,
                organization_id=organization_id,
                visible_units=visible_units,
                links=links,
                visible_slots=slots,
                runtime_slots=parent_slots,
                visible_assignments=assignments,
                runtime_assignments=parent_assignments,
                relations=page_relations,
                drift=runtime_definition_revision != organization.definition_revision,
            )
        return {
            "organization_id": organization.organization_id,
            "name": organization.name,
            "definition_revision": organization.definition_revision,
            "snapshot_hash": latest_snapshot.snapshot_hash if latest_snapshot else organization.plan_digest,
            "units": [
                {
                    "id": unit.id,
                    "unit_key": unit.unit_key,
                    "name": unit.name,
                    "unit_kind": unit.unit_kind,
                    "parent_unit_id": unit.parent_unit_id,
                    "parent_unit_key": (
                        all_unit_by_id[unit.parent_unit_id].unit_key if unit.parent_unit_id in all_unit_by_id else None
                    ),
                    "depth": _absolute_depth(unit, all_unit_by_id),
                    "child_count": len(children_by_parent.get(unit.id, [])),
                    "has_more_children": any(
                        child.id not in unit_by_id for child in children_by_parent.get(unit.id, [])
                    ),
                    "team_id": link_by_unit[unit.id].team_id if unit.id in link_by_unit else None,
                }
                for unit in visible_units
            ],
            "role_slots": [
                {
                    "id": slot.id,
                    "unit_id": slot.unit_id,
                    "slot_key": slot.slot_key,
                    "name": slot.slot_key,
                    "role_template_ref": f"{slot.role_template_key}@{slot.role_template_version}",
                    "default_count": slot.default_count,
                }
                for slot in slots
            ],
            "assignments": [
                {
                    "id": assignment.id,
                    "role_slot_id": assignment.role_slot_id,
                    "agent_url": safe_agent_endpoint_for_display(assignment.agent_url),
                }
                for assignment in assignments
            ],
            "relations": [
                {
                    "id": relation.id,
                    "kind": relation.kind,
                    "source_unit_id": relation.source_unit_id,
                    "target_unit_id": relation.target_unit_id,
                    "source_unit_key": unit_by_id[relation.source_unit_id].unit_key,
                    "target_unit_key": unit_by_id[relation.target_unit_id].unit_key,
                    "definition_relation_ref": relation.relation_key,
                }
                for relation in relations
            ],
            "runtime_definition_revision": runtime_definition_revision,
            "runtime_snapshot_hash": runtime_snapshot_hash,
            "runtime_nodes": runtime_nodes,
            "runtime_edges": runtime_edges,
            "diagnostics": [],
            "next_cursor": page_units[-1].unit_key if has_more and page_units else None,
        }

    def _list_for_ids(
        self,
        model,
        field,
        ids: list[str],
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> list[Any]:
        if not ids:
            return []
        statement = (
            select(model)
            .where(model.tenant_id == tenant_id)
            .where(model.project_id == project_id)
            .where(model.organization_id == organization_id)
            .where(field.in_(ids))
        )
        return list(self._session.exec(statement).all())

    def _runtime_projection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        visible_units: list[OrganizationUnitDB],
        links: list[OrganizationTeamLinkDB],
        visible_slots: list[OrganizationRoleSlotDB],
        runtime_slots: list[OrganizationRoleSlotDB],
        visible_assignments: list[OrganizationRoleAssignmentDB],
        runtime_assignments: list[OrganizationRoleAssignmentDB],
        relations: list[OrganizationRelationDB],
        drift: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        team_to_unit = {link.team_id: link.unit_id for link in links}
        unit_by_id = {unit.id: unit for unit in visible_units}
        runtime_unit_ids = sorted(
            {
                *(unit.id for unit in visible_units),
                *(slot.unit_id for slot in runtime_slots),
                *(team_to_unit.values()),
            }
        )
        dependencies = self._runtime_dependencies(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            team_ids=sorted(team_to_unit),
        )
        task_rows = self._runtime_task_rows(
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            unit_ids=runtime_unit_ids,
            team_ids=sorted(team_to_unit),
            role_slot_ids=sorted(slot.id for slot in runtime_slots),
        )

        visible_unit_ids = set(unit_by_id)
        visible_slot_ids = {slot.id for slot in visible_slots}
        visible_assignment_ids = {row.id for row in visible_assignments}
        assignment_by_binding = {(row.role_slot_id, row.agent_url): row.id for row in visible_assignments}
        task_states: dict[str, Counter[str]] = defaultdict(Counter)
        reason_codes: dict[str, set[str]] = defaultdict(set)
        blocker_refs: dict[str, set[str]] = defaultdict(set)
        gate_refs: dict[str, set[str]] = defaultdict(set)
        handoff_refs: dict[str, set[str]] = defaultdict(set)
        artifact_refs: dict[str, set[str]] = defaultdict(set)
        capacity_used: Counter[str] = Counter()
        capacity_limit: Counter[str] = Counter()

        for row in task_rows:
            node_ids: set[str] = set()
            unit_id = str(row.unit_id or "")
            if not unit_id:
                unit_id = team_to_unit.get(str(row.team_id or ""), "")
            if unit_id in visible_unit_ids:
                node_ids.add(unit_id)
            role_slot_id = str(row.role_slot_id or "")
            if role_slot_id in visible_slot_ids:
                node_ids.add(role_slot_id)
            assignment_id = assignment_by_binding.get((role_slot_id, str(row.assigned_agent_url or "")))
            if assignment_id in visible_assignment_ids:
                node_ids.add(assignment_id)

            status = _normalized_task_state(row.status)
            reason_code = str(row.status_reason_code or "").strip()
            for node_id in node_ids:
                task_states[node_id][status] += 1
                if status in _BLOCKED_TASK_STATES:
                    blocker_refs[node_id].add(f"task:{row.id}")
                if 0 < len(reason_code) <= 160:
                    reason_codes[node_id].add(reason_code)

        active_assignment_counts = Counter(row.role_slot_id for row in runtime_assignments if row.lifecycle == "active")
        for slot in runtime_slots:
            if slot.lifecycle != "active":
                continue
            maximum = slot.max_count if slot.max_count is not None else slot.default_count
            used = active_assignment_counts[slot.id]
            if slot.unit_id in visible_unit_ids:
                capacity_limit[slot.unit_id] += maximum
                capacity_used[slot.unit_id] += used
            if slot.id in visible_slot_ids:
                capacity_limit[slot.id] += maximum
                capacity_used[slot.id] += used

        handoff_relation_keys = {
            (relation.source_unit_id, relation.target_unit_id)
            for relation in relations
            if relation.handoff_definition_key
        }
        for dependency in dependencies:
            source_unit_id = team_to_unit.get(dependency.source_team_id, "")
            target_unit_id = team_to_unit.get(dependency.target_team_id, "")
            target_node_ids = {
                node_id
                for node_id in (target_unit_id, str(dependency.owner_ref or ""))
                if node_id in visible_unit_ids or node_id in visible_slot_ids
            }
            unresolved = dependency.status not in {"cancelled", "satisfied"}
            if dependency.status == "blocked" or (dependency.status == "pending" and dependency.blocking_reason):
                for node_id in target_node_ids:
                    blocker_refs[node_id].add(f"dependency:{dependency.id}")
            if unresolved and dependency.gate_ref:
                for node_id in target_node_ids:
                    gate_refs[node_id].add(str(dependency.gate_ref))
            if unresolved and (source_unit_id, target_unit_id) in handoff_relation_keys:
                for node_id in (source_unit_id, target_unit_id):
                    if node_id in visible_unit_ids:
                        handoff_refs[node_id].add(dependency.id)
                owner_ref = str(dependency.owner_ref or "")
                if owner_ref in visible_slot_ids:
                    handoff_refs[owner_ref].add(dependency.id)
            for node_id in target_node_ids:
                artifact_refs[node_id].update(
                    _bounded_reference(value)
                    for value in dependency.required_artifact_refs
                    if _bounded_reference(value)
                )

        artifacts_by_node = self._latest_artifacts(
            artifact_refs,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
        )
        projected_node_ids = visible_unit_ids | visible_slot_ids | visible_assignment_ids
        runtime_node_ids = (
            set(task_states)
            | set(capacity_limit)
            | set(capacity_used)
            | set(blocker_refs)
            | set(gate_refs)
            | set(handoff_refs)
            | set(artifacts_by_node)
        ) & projected_node_ids
        runtime_nodes = []
        for node_id in sorted(runtime_node_ids):
            status = _runtime_status(
                task_states[node_id],
                reason_codes=reason_codes[node_id],
                capacity_used=(capacity_used[node_id] if node_id in capacity_used else None),
                capacity_limit=(capacity_limit[node_id] if node_id in capacity_limit else None),
                blocker_count=len(blocker_refs[node_id]),
                gate_count=len(gate_refs[node_id]),
                handoff_count=len(handoff_refs[node_id]),
                drift=drift,
            )
            runtime_node = {"node_id": node_id, "status": status}
            if artifacts_by_node.get(node_id):
                runtime_node["latest_artifacts"] = artifacts_by_node[node_id]
            runtime_nodes.append(runtime_node)

        return runtime_nodes, self._runtime_edges(
            dependencies=dependencies,
            unit_by_id=unit_by_id,
            team_to_unit=team_to_unit,
        )

    def _runtime_dependencies(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        team_ids: list[str],
    ) -> list[CrossTeamTaskDependencyDB]:
        if not team_ids:
            return []
        statement = (
            select(CrossTeamTaskDependencyDB)
            .where(CrossTeamTaskDependencyDB.tenant_id == tenant_id)
            .where(CrossTeamTaskDependencyDB.project_id == project_id)
            .where(CrossTeamTaskDependencyDB.organization_id == organization_id)
            .where(
                sa.or_(
                    CrossTeamTaskDependencyDB.source_team_id.in_(team_ids),
                    CrossTeamTaskDependencyDB.target_team_id.in_(team_ids),
                )
            )
        )
        return list(self._session.exec(statement).all())

    def _runtime_task_rows(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        unit_ids: list[str],
        team_ids: list[str],
        role_slot_ids: list[str],
    ) -> list[Any]:
        bindings = []
        if unit_ids:
            bindings.append(TaskDB.unit_id.in_(unit_ids))
        if team_ids:
            bindings.append(TaskDB.team_id.in_(team_ids))
        if role_slot_ids:
            bindings.append(TaskDB.role_slot_id.in_(role_slot_ids))
        if not bindings:
            return []
        statement = (
            select(
                TaskDB.id,
                TaskDB.unit_id,
                TaskDB.team_id,
                TaskDB.role_slot_id,
                TaskDB.assigned_agent_url,
                TaskDB.status,
                TaskDB.status_reason_code,
                TaskDB.updated_at,
            )
            .where(TaskDB.tenant_id == tenant_id)
            .where(TaskDB.project_id == project_id)
            .where(TaskDB.organization_id == organization_id)
            .where(sa.or_(*bindings))
        )
        return list(self._session.exec(statement).all())

    def _latest_artifacts(
        self,
        refs_by_node: dict[str, set[str]],
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
    ) -> dict[str, list[dict[str, str]]]:
        refs = sorted({ref for values in refs_by_node.values() for ref in values})
        if not refs:
            return {}
        referenced_versions = list(
            self._session.exec(
                select(
                    ArtifactVersionDB.id,
                    ArtifactVersionDB.artifact_id,
                    ArtifactVersionDB.version_number,
                    ArtifactVersionDB.sha256,
                    ArtifactVersionDB.original_filename,
                    ArtifactVersionDB.created_at,
                    *_artifact_scope_projection(ArtifactVersionDB.version_metadata),
                ).where(ArtifactVersionDB.id.in_(refs))
            ).all()
        )
        artifact_ids = sorted(set(refs) | {str(row.artifact_id) for row in referenced_versions})
        artifacts = list(
            self._session.exec(
                select(
                    ArtifactDB.id,
                    ArtifactDB.latest_version_id,
                    ArtifactDB.latest_sha256,
                    ArtifactDB.latest_filename,
                    ArtifactDB.updated_at,
                    *_artifact_scope_projection(ArtifactDB.artifact_metadata),
                ).where(ArtifactDB.id.in_(artifact_ids))
            ).all()
        )
        if not artifacts:
            return {}
        latest_version_ids = sorted({str(row.latest_version_id) for row in artifacts if row.latest_version_id})
        latest_versions = (
            list(
                self._session.exec(
                    select(
                        ArtifactVersionDB.id,
                        ArtifactVersionDB.artifact_id,
                        ArtifactVersionDB.version_number,
                        ArtifactVersionDB.sha256,
                        ArtifactVersionDB.original_filename,
                        ArtifactVersionDB.created_at,
                        *_artifact_scope_projection(ArtifactVersionDB.version_metadata),
                    ).where(ArtifactVersionDB.id.in_(latest_version_ids))
                ).all()
            )
            if latest_version_ids
            else []
        )
        version_by_id = {str(row.id): row for row in latest_versions}
        artifact_by_id = {str(row.id): row for row in artifacts}
        alias_to_artifact = {artifact_id: artifact_id for artifact_id in artifact_by_id}
        alias_to_artifact.update({str(row.id): str(row.artifact_id) for row in referenced_versions})
        payload_by_artifact: dict[str, tuple[float, dict[str, str]]] = {}
        for artifact_id, artifact in artifact_by_id.items():
            latest_version_id = str(artifact.latest_version_id or "")
            version = version_by_id.get(latest_version_id)
            if not (
                _projected_artifact_scope_matches(
                    artifact,
                    tenant_id=tenant_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    metadata_fallback_field="artifact_metadata",
                )
                or (
                    version is not None
                    and _projected_artifact_scope_matches(
                        version,
                        tenant_id=tenant_id,
                        project_id=project_id,
                        organization_id=organization_id,
                        metadata_fallback_field="version_metadata",
                    )
                )
            ):
                continue
            digest = str((version.sha256 if version is not None else None) or artifact.latest_sha256 or "").strip()
            if not 32 <= len(digest) <= 128:
                continue
            version_label = str(version.version_number if version is not None else latest_version_id).strip()
            if not version_label or len(version_label) > 80:
                continue
            label = str(
                (version.original_filename if version is not None else None) or artifact.latest_filename or artifact_id
            ).strip()[:500]
            observed_at = float((version.created_at if version is not None else None) or artifact.updated_at or 0)
            payload_by_artifact[artifact_id] = (
                observed_at,
                {
                    "artifact_id": artifact_id,
                    "version": version_label,
                    "digest": digest,
                    "label": label,
                },
            )

        result: dict[str, list[dict[str, str]]] = {}
        for node_id, node_refs in refs_by_node.items():
            resolved_ids = {alias_to_artifact[ref] for ref in node_refs if ref in alias_to_artifact}
            resolved = sorted(
                (
                    payload_by_artifact[artifact_id]
                    for artifact_id in resolved_ids
                    if artifact_id in payload_by_artifact
                ),
                key=lambda item: (-item[0], item[1]["artifact_id"]),
            )
            if resolved:
                result[node_id] = [payload for _, payload in resolved[:_RUNTIME_ARTIFACTS_PER_NODE]]
        return result

    @staticmethod
    def _runtime_edges(
        *,
        dependencies: list[CrossTeamTaskDependencyDB],
        unit_by_id: dict[str, OrganizationUnitDB],
        team_to_unit: dict[str, str],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[CrossTeamTaskDependencyDB]] = defaultdict(list)
        for dependency in dependencies:
            if dependency.source_team_id not in team_to_unit or dependency.target_team_id not in team_to_unit:
                continue
            key = (
                team_to_unit[dependency.source_team_id],
                team_to_unit[dependency.target_team_id],
            )
            grouped[key].append(dependency)
        return [
            {
                "id": f"runtime-task-dependency:{source_unit_id}:{target_unit_id}",
                "kind": "runtime_task_dependency",
                "source_node_id": source_unit_id,
                "target_node_id": target_unit_id,
                "runtime_ref": "cross_team_task_dependencies",
                "provenance_count": len(rows),
                "drill_down_refs": sorted(row.id for row in rows),
            }
            for (source_unit_id, target_unit_id), rows in sorted(grouped.items())
            if source_unit_id in unit_by_id and target_unit_id in unit_by_id
        ]


def _artifact_scope_matches(
    metadata: object,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
) -> bool:
    """Require an explicit immutable organization binding for overlay data."""

    if not isinstance(metadata, Mapping):
        return False
    candidates = [metadata]
    for key in ("organization_scope", "organization_binding", "scope"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    return any(
        str(candidate.get("tenant_id") or "") == tenant_id
        and str(candidate.get("project_id") or "") == project_id
        and str(candidate.get("organization_id") or "") == organization_id
        for candidate in candidates
    )


_ARTIFACT_SCOPE_CONTAINERS = (
    "direct",
    "organization_scope",
    "organization_binding",
    "scope",
)


def _artifact_scope_projection(column: Any) -> tuple[Any, ...]:
    """Select only scope identifiers from artifact JSON, never its payload."""

    expressions: list[Any] = []
    for container in _ARTIFACT_SCOPE_CONTAINERS:
        candidate = column if container == "direct" else column[container]
        for field in ("tenant_id", "project_id", "organization_id"):
            expressions.append(candidate[field].as_string().label(f"artifact_scope_{container}_{field}"))
    return tuple(expressions)


def _projected_artifact_scope_matches(
    row: Any,
    *,
    tenant_id: str,
    project_id: str,
    organization_id: str,
    metadata_fallback_field: str,
) -> bool:
    """Validate projected identifiers; the fallback supports test adapters."""

    expected = (tenant_id, project_id, organization_id)
    for container in _ARTIFACT_SCOPE_CONTAINERS:
        actual = tuple(
            str(
                getattr(
                    row,
                    f"artifact_scope_{container}_{field}",
                    "",
                )
                or ""
            )
            for field in ("tenant_id", "project_id", "organization_id")
        )
        if actual == expected:
            return True

    # Lightweight repository fakes may return ORM objects even for projected
    # column queries.  Production SQL rows use the bounded fields above.
    return _artifact_scope_matches(
        getattr(row, metadata_fallback_field, None),
        tenant_id=tenant_id,
        project_id=project_id,
        organization_id=organization_id,
    )


def _normalized_task_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return state if state else "unknown"


def _bounded_reference(value: Any) -> str:
    reference = str(value or "").strip()
    return reference if 0 < len(reference) <= 191 else ""


def _runtime_status(
    task_states: Counter[str],
    *,
    reason_codes: set[str],
    capacity_used: int | None,
    capacity_limit: int | None,
    blocker_count: int,
    gate_count: int,
    handoff_count: int,
    drift: bool,
) -> dict[str, Any]:
    if blocker_count:
        state = "blocked"
    elif sum(task_states[value] for value in _FAILED_TASK_STATES):
        state = "failed"
    elif sum(task_states[value] for value in _ACTIVE_TASK_STATES):
        state = "active"
    elif sum(task_states[value] for value in _PENDING_TASK_STATES):
        state = "pending"
    elif task_states and all(value in _COMPLETED_TASK_STATES | _CANCELLED_TASK_STATES for value in task_states):
        state = "completed" if any(value in _COMPLETED_TASK_STATES for value in task_states) else "cancelled"
    elif task_states:
        state = "unknown"
    elif gate_count or handoff_count:
        state = "pending"
    elif capacity_limit is not None:
        state = "ready"
    else:
        state = "idle"
    labels = {
        "active": "In progress",
        "blocked": "Blocked",
        "cancelled": "Cancelled",
        "completed": "Completed",
        "failed": "Failed",
        "idle": "Idle",
        "pending": "Pending",
        "ready": "Ready",
        "unknown": "Unknown",
    }
    status: dict[str, Any] = {
        "state": state,
        "label": labels[state],
        "blocker_count": blocker_count,
        "gate_count": gate_count,
        "handoff_count": handoff_count,
        "drift": drift,
    }
    if capacity_used is not None:
        status["capacity_used"] = capacity_used
    if capacity_limit is not None:
        status["capacity_limit"] = capacity_limit
    if reason_codes and state in {"blocked", "failed"}:
        status["reason_code"] = sorted(reason_codes)[0]
    return status


def _absolute_depth(unit: OrganizationUnitDB, unit_by_id: dict[str, OrganizationUnitDB]) -> int:
    depth = 1
    parent_id = unit.parent_unit_id
    visited = {unit.id}
    while parent_id:
        if parent_id in visited or parent_id not in unit_by_id:
            return depth
        visited.add(parent_id)
        depth += 1
        parent_id = unit_by_id[parent_id].parent_unit_id
    return depth


def _within_requested_depth(
    unit: OrganizationUnitDB,
    unit_by_id: dict[str, OrganizationUnitDB],
    *,
    root_unit: OrganizationUnitDB | None,
    max_depth: int,
) -> bool:
    if root_unit is None:
        return _absolute_depth(unit, unit_by_id) <= max_depth
    distance = 0
    current: OrganizationUnitDB | None = unit
    visited: set[str] = set()
    while current is not None and current.id not in visited:
        if current.id == root_unit.id:
            return distance <= max_depth
        visited.add(current.id)
        distance += 1
        current = unit_by_id.get(current.parent_unit_id) if current.parent_unit_id else None
    return False


__all__ = ["SqlOrganizationTopologyReadRepository"]
