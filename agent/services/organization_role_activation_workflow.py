"""Revision-bound team workflow projection for the Organization role activation read model.

Resolves immutable team blueprints, workflows and handoff contracts and
projects workflow steps, role bindings and intra/cross-team edges. Live
execution facts are layered on separately by the runtime projection.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from agent.db_models.organizations import (
    OrganizationRelationDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationUnitDB,
)
from agent.models.organization_models import VersionedDefinitionRef
from agent.services.organization_definition_catalog_service import (
    FileCatalogDefinitionRepositoryAdapter,
)
from agent.services.organization_role_activation_errors import OrganizationRoleActivationReadError

ROUTER_OWNER = "hub"


def project_team_workflows(
    *,
    resolver: FileCatalogDefinitionRepositoryAdapter,
    tenant_id: str,
    project_id: str,
    units: Sequence[OrganizationUnitDB],
    slots: Sequence[OrganizationRoleSlotDB],
    assignments: Sequence[OrganizationRoleAssignmentDB],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    units_by_blueprint: dict[str, list[OrganizationUnitDB]] = defaultdict(list)
    for unit in units:
        units_by_blueprint[_unit_blueprint_ref(unit)].append(unit)
    slots_by_unit: dict[str, list[OrganizationRoleSlotDB]] = defaultdict(list)
    for slot in slots:
        slots_by_unit[slot.unit_id].append(slot)
    assignment_count = Counter(row.role_slot_id for row in assignments)

    teams: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for unit in units:
        blueprint_ref = _unit_blueprint_ref(unit)
        blueprint_row = resolver.get_team_blueprint(
            tenant_id,
            project_id,
            str(unit.team_blueprint_key),
            int(unit.team_blueprint_version or 0),
        )
        if blueprint_row is None:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_team_definition_missing",
                details={"team_blueprint_ref": blueprint_ref},
            )
        team_definition = _team_definition(blueprint_row)
        workflow_ref = _definition_ref(
            team_definition.get("workflow_ref") or _row_workflow_ref(blueprint_row),
            reason_code="organization_role_activation_workflow_reference_invalid",
        )
        workflow_row = resolver.get_workflow(
            tenant_id,
            project_id,
            workflow_ref.key,
            workflow_ref.version,
        )
        if workflow_row is None:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_workflow_definition_missing",
                details={"workflow_ref": workflow_ref.portable_ref()},
            )
        workflow = _workflow_definition(workflow_row)
        steps, team_edges = _workflow_steps(
            unit=unit,
            owning_blueprint_ref=blueprint_ref,
            workflow_ref=workflow_ref.portable_ref(),
            workflow=workflow,
            units_by_blueprint=units_by_blueprint,
            slots_by_unit=slots_by_unit,
            assignment_count=assignment_count,
        )
        edges.extend(team_edges)
        teams.append(
            {
                "team_unit_id": unit.id,
                "team_unit_key": unit.unit_key,
                "team_name": unit.name,
                "team_blueprint_ref": blueprint_ref,
                "lifecycle": unit.lifecycle,
                "revision_binding": {
                    "team_blueprint_content_hash": str(getattr(blueprint_row, "content_hash", "") or ""),
                    "workflow_content_hash": str(getattr(workflow_row, "content_hash", "") or ""),
                },
                "workflow": {
                    "workflow_ref": workflow_ref.portable_ref(),
                    "mode": str(workflow.get("mode") or ""),
                    "default_failure_policy": str(workflow.get("default_failure_policy") or "block"),
                    "steps": steps,
                },
            }
        )
    edges.sort(
        key=lambda edge: (
            edge["type"],
            edge["source"]["ref"],
            edge["target"]["ref"],
        )
    )
    return teams, edges


def _workflow_steps(
    *,
    unit: OrganizationUnitDB,
    owning_blueprint_ref: str,
    workflow_ref: str,
    workflow: Mapping[str, Any],
    units_by_blueprint: Mapping[str, Sequence[OrganizationUnitDB]],
    slots_by_unit: Mapping[str, Sequence[OrganizationRoleSlotDB]],
    assignment_count: Counter[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_steps = workflow.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise OrganizationRoleActivationReadError(
            "organization_role_activation_workflow_steps_missing",
            details={"workflow_ref": workflow_ref},
        )
    normalized: list[dict[str, Any]] = []
    step_by_id: dict[str, dict[str, Any]] = {}
    for raw_step in raw_steps:
        if not isinstance(raw_step, Mapping):
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_workflow_step_invalid",
                details={"workflow_ref": workflow_ref},
            )
        step = _normalize_step(raw_step, workflow=workflow)
        step_id = step["step_id"]
        if not step_id or step_id in step_by_id:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_workflow_step_invalid",
                details={"workflow_ref": workflow_ref, "step_id": step_id},
            )
        step["step_ref"] = _step_ref(unit.id, workflow_ref, step_id)
        normalized.append(step)
        step_by_id[step_id] = step

    for step in normalized:
        missing_dependencies = sorted(set(step["depends_on"]) - set(step_by_id))
        if missing_dependencies:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_workflow_dependency_missing",
                details={
                    "workflow_ref": workflow_ref,
                    "step_id": step["step_id"],
                    "depends_on": missing_dependencies,
                },
            )
        target_resolution = _target_resolution(
            unit=unit,
            owning_blueprint_ref=owning_blueprint_ref,
            selector=step["target_team_selector"],
            units_by_blueprint=units_by_blueprint,
        )
        step["target_resolution"] = target_resolution
        step["role_binding"] = _role_binding(
            owner_role_ref=step["owner_role_ref"],
            target_resolution=target_resolution,
            slots_by_unit=slots_by_unit,
            assignment_count=assignment_count,
        )
        dependency_refs = [step_by_id[dependency]["step_ref"] for dependency in step["depends_on"]]
        ancestor_ids = _ancestor_step_ids(str(step["step_id"]), step_by_id)
        predecessor_outputs = {output for ancestor_id in ancestor_ids for output in step_by_id[ancestor_id]["outputs"]}
        step["activation"] = {
            "state": "not_observed",
            "reason_code": "organization_role_activation_runtime_not_observed",
            "router_owner": ROUTER_OWNER,
            "rule": ("hub_route_on_workflow_start" if not dependency_refs else "hub_route_after_dependencies"),
            "reacts_to": (
                [
                    {
                        "kind": "hub_workflow_intake",
                        "source_ref": "hub",
                        "source_owner_role_ref": None,
                    }
                ]
                if not dependency_refs
                else [
                    {
                        "kind": "workflow_step_completion",
                        "source_ref": step_by_id[dependency]["step_ref"],
                        "source_owner_role_ref": step_by_id[dependency]["owner_role_ref"],
                    }
                    for dependency in step["depends_on"]
                ]
            ),
            "external_inputs": sorted(set(step["inputs"]) - predecessor_outputs),
        }

    edges = _workflow_edges(normalized)
    return normalized, edges


def _ancestor_step_ids(
    step_id: str,
    step_by_id: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    ancestors: set[str] = set()
    pending = list(step_by_id[step_id]["depends_on"])
    while pending:
        candidate = str(pending.pop())
        if candidate in ancestors:
            continue
        ancestors.add(candidate)
        pending.extend(step_by_id[candidate]["depends_on"])
    return ancestors


def cross_team_artifact_edges(
    *,
    teams: Sequence[dict[str, Any]],
    units: Sequence[OrganizationUnitDB],
    relations: Sequence[OrganizationRelationDB],
    handoff_definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    teams_by_id = {str(team["team_unit_id"]): team for team in teams}
    children: dict[str, list[str]] = defaultdict(list)
    for unit in units:
        if unit.parent_unit_id:
            children[unit.parent_unit_id].append(unit.id)

    def endpoint_team_ids(unit_id: str) -> list[str]:
        found: list[str] = []
        pending = [unit_id]
        seen: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate in teams_by_id:
                found.append(candidate)
            pending.extend(children.get(candidate, ()))
        return sorted(found)

    edges_by_id: dict[str, dict[str, Any]] = {}
    sources_by_target_step: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = defaultdict(dict)
    for relation in relations:
        handoff_ref = f"{relation.handoff_definition_key}@{relation.handoff_definition_version}"
        handoff = handoff_definitions[handoff_ref]
        declared_handoff = _edge(
            edge_type="declares_handoff",
            source_kind=("team_unit" if relation.source_unit_id in teams_by_id else "organization_unit"),
            source_ref=relation.source_unit_id,
            target_kind=("team_unit" if relation.target_unit_id in teams_by_id else "organization_unit"),
            target_ref=relation.target_unit_id,
            reason_code="organization_relation_handoff_declared",
            metadata={
                "relation_key": relation.relation_key,
                "handoff_ref": handoff_ref,
                "dependency_policy": relation.dependency_policy,
                "required_artifact_kinds": list(handoff["required_artifact_kinds"]),
                "acceptance_gate_ref": handoff["acceptance_gate_ref"],
            },
        )
        edges_by_id[declared_handoff["edge_id"]] = declared_handoff
        for source_team_id in endpoint_team_ids(relation.source_unit_id):
            source_steps = teams_by_id[source_team_id]["workflow"]["steps"]
            for target_team_id in endpoint_team_ids(relation.target_unit_id):
                if source_team_id == target_team_id:
                    continue
                target_steps = teams_by_id[target_team_id]["workflow"]["steps"]
                for source in source_steps:
                    if source.get("handoff_ref") != handoff_ref:
                        continue
                    for target in target_steps:
                        artifacts = sorted(set(target["activation"]["external_inputs"]) & set(source["outputs"]))
                        if not artifacts:
                            continue
                        source_info = {
                            "artifacts": artifacts,
                            "source_step_ref": str(source["step_ref"]),
                            "source_owner_role_ref": str(source["owner_role_ref"]),
                            "source_team_unit_id": source_team_id,
                            "handoff_ref": handoff_ref,
                            "relation_key": relation.relation_key,
                        }
                        source_identity = (
                            source_info["source_step_ref"],
                            handoff_ref,
                            tuple(artifacts),
                            relation.relation_key,
                        )
                        sources_by_target_step[str(target["step_ref"])][source_identity] = source_info
                        edge = _edge(
                            edge_type="produces_input",
                            source_kind="workflow_step",
                            source_ref=str(source["step_ref"]),
                            target_kind="workflow_step",
                            target_ref=str(target["step_ref"]),
                            reason_code="organization_cross_team_handoff_input_declared",
                            metadata={
                                "artifacts": artifacts,
                                "handoff_ref": handoff_ref,
                                "relation_key": relation.relation_key,
                                "source_team_unit_id": source_team_id,
                                "target_team_unit_id": target_team_id,
                            },
                        )
                        edges_by_id[edge["edge_id"]] = edge

    for team in teams:
        for target in team["workflow"]["steps"]:
            sources = sources_by_target_step.get(str(target["step_ref"]))
            if sources:
                target["activation"]["declared_input_sources"] = sorted(
                    sources.values(),
                    key=lambda item: (
                        item["source_step_ref"],
                        item["handoff_ref"],
                        item["relation_key"],
                        item["artifacts"],
                    ),
                )
    return list(edges_by_id.values())


def resolve_handoff_definitions(
    *,
    resolver: FileCatalogDefinitionRepositoryAdapter,
    tenant_id: str,
    project_id: str,
    relations: Sequence[OrganizationRelationDB],
) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for relation in relations:
        key = str(relation.handoff_definition_key or "")
        version = int(relation.handoff_definition_version or 0)
        portable_ref = f"{key}@{version}"
        if portable_ref in definitions:
            continue
        row = resolver.get_handoff(tenant_id, project_id, key, version)
        if row is None:
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_handoff_definition_missing",
                details={"handoff_ref": portable_ref},
            )
        payload = getattr(row, "definition_json", None)
        definition = dict(payload) if isinstance(payload, Mapping) else {}
        required_artifact_kinds = definition.get(
            "required_artifact_kinds",
            getattr(row, "required_artifact_kinds", None),
        )
        acceptance_gate_ref = definition.get(
            "acceptance_gate_ref",
            getattr(row, "acceptance_gate_ref", None),
        )
        if (
            not isinstance(required_artifact_kinds, list)
            or any(not isinstance(value, str) or not value.strip() for value in required_artifact_kinds)
            or not isinstance(acceptance_gate_ref, str)
            or not acceptance_gate_ref.strip()
        ):
            raise OrganizationRoleActivationReadError(
                "organization_role_activation_handoff_definition_invalid",
                details={"handoff_ref": portable_ref},
            )
        definitions[portable_ref] = {
            "required_artifact_kinds": list(required_artifact_kinds),
            "acceptance_gate_ref": acceptance_gate_ref,
        }
    return definitions


def _normalize_step(
    raw_step: Mapping[str, Any],
    *,
    workflow: Mapping[str, Any],
) -> dict[str, Any]:
    selector = raw_step.get("target_team_selector")
    gate = raw_step.get("gate")
    if not isinstance(selector, Mapping) or not isinstance(gate, Mapping):
        raise OrganizationRoleActivationReadError("organization_role_activation_workflow_step_invalid")
    cardinality = selector.get("cardinality")
    if isinstance(cardinality, bool) or not isinstance(cardinality, int) or cardinality < 1:
        raise OrganizationRoleActivationReadError("organization_role_activation_workflow_step_invalid")
    return {
        "step_id": str(raw_step.get("step_id") or "").strip(),
        "title": str(raw_step.get("title") or "").strip(),
        "task_kind": str(raw_step.get("task_kind") or "").strip(),
        "owner_role_ref": str(raw_step.get("owner_role_ref") or "").strip(),
        "target_team_selector": {
            "team_blueprint_ref": str(selector.get("team_blueprint_ref") or "").strip(),
            "cardinality": cardinality,
            "routing": str(selector.get("routing") or "").strip(),
        },
        "depends_on": _string_list(raw_step.get("depends_on")),
        "inputs": _string_list(raw_step.get("inputs")),
        "outputs": _string_list(raw_step.get("outputs")),
        "gate": {
            "required": bool(gate.get("required")),
            "acceptance_checks": _string_list(gate.get("acceptance_checks")),
            "approval_role_ref": (
                str(gate.get("approval_role_ref")).strip() if gate.get("approval_role_ref") else None
            ),
            "independent_principal_required": bool(gate.get("independent_principal_required")),
        },
        "failure_policy": str(raw_step.get("failure_policy") or workflow.get("default_failure_policy") or "block"),
        "handoff_ref": (str(raw_step.get("handoff_ref")).strip() if raw_step.get("handoff_ref") else None),
    }


def _target_resolution(
    *,
    unit: OrganizationUnitDB,
    owning_blueprint_ref: str,
    selector: Mapping[str, Any],
    units_by_blueprint: Mapping[str, Sequence[OrganizationUnitDB]],
) -> dict[str, Any]:
    target_ref = str(selector.get("team_blueprint_ref") or "")
    cardinality = int(selector.get("cardinality") or 0)
    candidates = sorted(
        units_by_blueprint.get(target_ref, ()),
        key=lambda candidate: (candidate.unit_key, candidate.id),
    )
    candidate_ids = [candidate.id for candidate in candidates]
    if target_ref == owning_blueprint_ref and cardinality == 1:
        selected_ids = [unit.id]
        state = "bound"
        reason_code = "organization_role_activation_owning_team_bound"
    elif len(candidates) == cardinality:
        selected_ids = candidate_ids
        state = "bound"
        reason_code = "organization_role_activation_candidate_set_bound"
    elif len(candidates) >= cardinality:
        selected_ids = []
        state = "hub_selection_required"
        reason_code = "organization_role_activation_hub_selection_required"
    else:
        selected_ids = []
        state = "unsatisfied"
        reason_code = "organization_role_activation_target_cardinality_unsatisfied"
    return {
        "state": state,
        "reason_code": reason_code,
        "router_owner": ROUTER_OWNER,
        "candidate_team_unit_ids": candidate_ids,
        "bound_team_unit_ids": selected_ids,
    }


def _role_binding(
    *,
    owner_role_ref: str,
    target_resolution: Mapping[str, Any],
    slots_by_unit: Mapping[str, Sequence[OrganizationRoleSlotDB]],
    assignment_count: Counter[str],
) -> dict[str, Any]:
    candidate_unit_ids = set(target_resolution["candidate_team_unit_ids"])
    bound_unit_ids = set(target_resolution["bound_team_unit_ids"])
    candidate_slots = sorted(
        (
            slot
            for unit_id in candidate_unit_ids
            for slot in slots_by_unit.get(unit_id, ())
            if f"{slot.role_template_key}@{slot.role_template_version}" == owner_role_ref
        ),
        key=lambda slot: (slot.unit_id, slot.slot_key, slot.id),
    )
    bound_slots = [slot for slot in candidate_slots if slot.unit_id in bound_unit_ids]
    if not candidate_slots:
        state = "unavailable"
        reason_code = "organization_role_activation_owner_role_unavailable"
    elif not bound_unit_ids:
        state = "candidate_only"
        reason_code = "organization_role_activation_role_hub_selection_pending"
    elif not bound_slots:
        state = "unavailable"
        reason_code = "organization_role_activation_owner_role_unavailable"
    else:
        state = "bound"
        reason_code = "organization_role_activation_owner_role_bound"
    return {
        "state": state,
        "reason_code": reason_code,
        "owner_role_ref": owner_role_ref,
        "candidate_role_slot_ids": [slot.id for slot in candidate_slots],
        "bound_role_slot_ids": [slot.id for slot in bound_slots],
        "assignment_coverage": _assignment_coverage(
            bound_slots,
            assignment_count=assignment_count,
        ),
    }


def _workflow_edges(
    steps: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    by_id = {str(step["step_id"]): step for step in steps}
    for target in steps:
        for dependency in target["depends_on"]:
            source = by_id[str(dependency)]
            edges.append(
                _edge(
                    edge_type="unblocks",
                    source_kind="workflow_step",
                    source_ref=str(source["step_ref"]),
                    target_kind="workflow_step",
                    target_ref=str(target["step_ref"]),
                    reason_code="organization_workflow_dependency_declared",
                    metadata={},
                )
            )
        ancestor_ids = _ancestor_step_ids(str(target["step_id"]), by_id)
        for source in steps:
            if str(source["step_id"]) not in ancestor_ids:
                continue
            artifacts = sorted(set(source["outputs"]) & set(target["inputs"]))
            if artifacts:
                edges.append(
                    _edge(
                        edge_type="produces_input",
                        source_kind="workflow_step",
                        source_ref=str(source["step_ref"]),
                        target_kind="workflow_step",
                        target_ref=str(target["step_ref"]),
                        reason_code="organization_workflow_artifact_flow_declared",
                        metadata={"artifacts": artifacts},
                    )
                )
        gate = dict(target["gate"])
        if gate.get("required"):
            approval_role_ref = str(gate.get("approval_role_ref") or "")
            edges.append(
                _edge(
                    edge_type="requires_gate",
                    source_kind="workflow_step",
                    source_ref=str(target["step_ref"]),
                    target_kind=("role_template" if approval_role_ref else "hub"),
                    target_ref=(approval_role_ref or "hub"),
                    reason_code="organization_workflow_gate_declared",
                    metadata={
                        "acceptance_checks": list(gate.get("acceptance_checks") or []),
                        "independent_principal_required": bool(gate.get("independent_principal_required")),
                    },
                )
            )
    return edges


def _edge(
    *,
    edge_type: str,
    source_kind: str,
    source_ref: str,
    target_kind: str,
    target_ref: str,
    reason_code: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    identity = json.dumps(
        [
            edge_type,
            source_kind,
            source_ref,
            target_kind,
            target_ref,
            str(metadata.get("relation_key") or ""),
            str(metadata.get("handoff_ref") or ""),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    edge_id = f"activation-edge-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
    return {
        "edge_id": edge_id,
        "type": edge_type,
        "source": {"kind": source_kind, "ref": source_ref},
        "target": {"kind": target_kind, "ref": target_ref},
        "reason_code": reason_code,
        "metadata": dict(metadata),
    }


def _team_definition(row: Any) -> dict[str, Any]:
    value = getattr(row, "definition_json", None)
    if not isinstance(value, Mapping):
        raise OrganizationRoleActivationReadError("organization_role_activation_team_definition_invalid")
    return dict(value)


def _workflow_definition(row: Any) -> dict[str, Any]:
    value = getattr(row, "definition_json", None)
    if isinstance(value, Mapping):
        return dict(value)
    steps = getattr(row, "steps_json", None)
    if not isinstance(steps, list):
        raise OrganizationRoleActivationReadError("organization_role_activation_workflow_definition_invalid")
    return {
        "key": str(getattr(row, "definition_key", "") or ""),
        "version": int(getattr(row, "version", 0) or 0),
        "mode": str(getattr(row, "mode", "") or ""),
        "default_failure_policy": str(getattr(row, "default_failure_policy", "") or ""),
        "steps": list(steps),
    }


def _row_workflow_ref(row: Any) -> str:
    key = str(getattr(row, "workflow_definition_key", "") or "")
    version = int(getattr(row, "workflow_definition_version", 0) or 0)
    return f"{key}@{version}" if key and version else ""


def _definition_ref(value: Any, *, reason_code: str) -> VersionedDefinitionRef:
    try:
        return VersionedDefinitionRef.parse(str(value or ""))
    except ValueError as exc:
        raise OrganizationRoleActivationReadError(reason_code) from exc


def _unit_blueprint_ref(unit: OrganizationUnitDB) -> str:
    return f"{unit.team_blueprint_key}@{unit.team_blueprint_version}"


def _step_ref(unit_id: str, workflow_ref: str, step_id: str) -> str:
    return f"team:{unit_id}/workflow:{workflow_ref}/step:{step_id}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _assignment_coverage(
    slots: Sequence[OrganizationRoleSlotDB],
    *,
    assignment_count: Counter[str],
) -> dict[str, Any]:
    required = sum(slot.min_count for slot in slots)
    desired = sum(slot.default_count for slot in slots)
    active = sum(assignment_count[slot.id] for slot in slots)
    if not slots:
        state = "not_bound"
        reason_code = "organization_role_activation_assignment_not_bound"
    elif active >= desired:
        state = "desired_covered"
        reason_code = "organization_role_activation_assignment_desired_covered"
    elif active >= required:
        state = "minimum_covered"
        reason_code = "organization_role_activation_assignment_minimum_covered"
    elif active == 0:
        state = "unassigned"
        reason_code = "organization_role_activation_assignment_unassigned"
    else:
        state = "understaffed"
        reason_code = "organization_role_activation_assignment_understaffed"
    return {
        "state": state,
        "reason_code": reason_code,
        "required_count": required,
        "desired_count": desired,
        "active_count": active,
    }


__all__ = [
    "ROUTER_OWNER",
    "cross_team_artifact_edges",
    "project_team_workflows",
    "resolve_handoff_definitions",
]
