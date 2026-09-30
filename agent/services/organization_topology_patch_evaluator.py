"""Write-free, ordered interpreter that evaluates a topology patch into a digest-bound preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from agent.models.organization_models import (
    AssignmentPolicyDefinition,
    OrganizationDiagnostic,
    SeparationOfDutiesDefinition,
    canonical_sha256,
)
from agent.services.organization_assignment_eligibility_service import (
    OrganizationAssignmentEligibilityService,
)
from agent.services.organization_blueprint_validation_service import (
    PARENT_KIND_MATRIX,
    RELATION_ENDPOINT_KIND_MATRIX,
)
from agent.services.organization_limit_projection import organization_limit_profile_projection
from agent.services.organization_slot_separation_service import (
    OrganizationSlotSeparationPolicy,
    evaluate_organization_slot_separation,
)
from agent.services.organization_topology_patch_contracts import (
    OrganizationTopologyPatchPreview,
    TopologyAddOperation,
    TopologyConnectOperation,
    TopologyRemoveOperation,
    TopologyReparentOperation,
)
from agent.services.organization_topology_patch_graph import (
    aggregate_subtree_activity,
    has_parent_cycle,
    has_relation_cycle,
    planned_node_id,
    unit_subtree_ids,
)


@dataclass(frozen=True, slots=True)
class TopologyPatchEvaluation:
    preview: OrganizationTopologyPatchPreview
    unit_activity: dict[str, dict[str, int]]


class OrganizationTopologyPatchEvaluator:
    """Evaluate a patch document against one loaded Organization state without writing."""

    def __init__(self, *, assignment_eligibility: OrganizationAssignmentEligibilityService) -> None:
        self._assignment_eligibility = assignment_eligibility

    def evaluate(  # noqa: C901 - one ordered interpreter preserves cross-operation draft state
        self, *, state, tenant_id, project_id, organization_id, principal_id, document, limits, expires_at_epoch
    ):
        diagnostics: list[OrganizationDiagnostic] = []
        planned_writes: list[str] = []

        def issue(path, code, message, *, severity="blocker", **details):
            diagnostics.append(
                OrganizationDiagnostic(
                    path=path,
                    reason_code=code,
                    human_message=message,
                    severity=severity,
                    details=details,
                )
            )

        if state.organization.definition_revision != document.expected_revision:
            issue("$.expected_revision", "ORGANIZATION_PATCH_REVISION_STALE", "If-Match definition revision is stale.")
        if state.snapshot is None:
            issue("$", "ORGANIZATION_PATCH_SNAPSHOT_MISSING", "Organization has no revision-bound topology snapshot.")
            source_snapshot_hash = "missing"
        else:
            source_snapshot_hash = state.snapshot.snapshot_hash
        if len(document.operations) > limits.max_patch_operations:
            issue(
                "$.operations",
                "ORGANIZATION_PATCH_OPERATION_LIMIT_EXCEEDED",
                "Patch exceeds the effective operation limit.",
            )
        if state.budget_policy_hash is None:
            issue("$", "ORGANIZATION_BUDGET_POLICY_MISSING", "Bound organization budget policy is unavailable.")

        units = {
            row.id: {
                "id": row.id,
                "key": row.unit_key,
                "kind": row.unit_kind,
                "parent_id": row.parent_unit_id,
                "lifecycle": row.lifecycle,
                "team_ref": (
                    f"{row.team_blueprint_key}@{row.team_blueprint_version}"
                    if row.team_blueprint_key and row.team_blueprint_version
                    else None
                ),
            }
            for row in state.units
            if row.lifecycle != "archived"
        }
        reserved_stable_keys = {row.unit_key for row in state.units}
        reserved_slot_keys = {(row.unit_id, row.slot_key) for row in state.role_slots}
        reserved_node_ids = {row.id for row in (*state.units, *state.role_slots)}
        slots = {
            row.id: {
                "id": row.id,
                "unit_id": row.unit_id,
                "key": row.slot_key,
                "required": row.required,
                "default_count": row.default_count,
                "max_count": row.max_count,
                "assignment_policy": dict(row.assignment_policy or {}),
                "sod": dict(row.separation_of_duties or {}),
                "lifecycle": row.lifecycle,
            }
            for row in state.role_slots
            if row.lifecycle != "archived"
        }
        assignments = [
            {"id": row.id, "slot_id": row.role_slot_id, "agent_id": row.agent_url}
            for row in state.assignments
            if row.lifecycle == "active"
        ]
        historical_assignments = {
            (row.role_slot_id, row.agent_url): row for row in state.assignments if row.lifecycle != "active"
        }
        relations = {
            row.id: {
                "id": row.id,
                "key": row.relation_key,
                "kind": row.kind,
                "source_id": row.source_unit_id,
                "target_id": row.target_unit_id,
                "dependency_policy": row.dependency_policy,
            }
            for row in state.relations
            if row.lifecycle == "active"
        }
        reserved_relation_keys = {row.relation_key for row in state.relations}
        reserved_relation_identities = {
            (row.kind, row.source_unit_id, row.target_unit_id) for row in state.relations if row.lifecycle == "active"
        }
        global_assignment_counts = dict(state.global_assignment_count_by_agent)

        for index, operation in enumerate(document.operations):
            path = f"$.operations[{index}]"
            if isinstance(operation, TopologyAddOperation):
                parent = units.get(operation.parent_id)
                if parent is None:
                    issue(
                        f"{path}.parent_id",
                        "ORGANIZATION_PATCH_PARENT_NOT_FOUND",
                        "Add parent is outside the active topology.",
                    )
                    continue
                planned_id = planned_node_id(organization_id, operation.node_kind, operation.value.stable_key)
                if (
                    operation.value.stable_key in reserved_stable_keys
                    or planned_id in reserved_node_ids
                    or (
                        operation.node_kind == "role_slot"
                        and (operation.parent_id, str(operation.value.slot_key)) in reserved_slot_keys
                    )
                ):
                    issue(
                        f"{path}.value.stable_key",
                        "ORGANIZATION_PATCH_STABLE_KEY_CONFLICT",
                        "Stable key already exists in scope.",
                    )
                    continue
                if operation.node_kind == "role_slot":
                    if parent["kind"] != "team":
                        issue(
                            f"{path}.parent_id",
                            "ORGANIZATION_ROLE_SLOT_PARENT_INVALID",
                            "Role slots require a team parent.",
                        )
                        continue
                    if operation.value.role_template_ref not in state.role_template_refs:
                        issue(
                            f"{path}.value.role_template_ref",
                            "ROLE_TEMPLATE_NOT_FOUND",
                            "Role template revision is missing.",
                        )
                        continue
                    slots[planned_id] = {
                        "id": planned_id,
                        "unit_id": operation.parent_id,
                        "key": operation.value.slot_key,
                        "required": operation.value.required,
                        "default_count": operation.value.default_count,
                        "max_count": operation.value.max_count,
                        "assignment_policy": operation.value.assignment_policy.model_dump(mode="json"),
                        "sod": operation.value.separation_of_duties.model_dump(mode="json"),
                        "lifecycle": "active",
                    }
                    reserved_stable_keys.add(operation.value.stable_key)
                    reserved_slot_keys.add((operation.parent_id, str(operation.value.slot_key)))
                    reserved_node_ids.add(planned_id)
                    planned_writes.append(f"role_slot:create:{planned_id}")
                    continue
                if operation.node_kind not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
                    issue(
                        f"{path}.parent_id",
                        "ORGANIZATION_PARENT_KIND_INVALID",
                        "Parent and child kinds are incompatible.",
                    )
                    continue
                team_ref = operation.value.team_blueprint_ref
                if operation.node_kind == "team" and team_ref not in state.team_blueprints:
                    issue(
                        f"{path}.value.team_blueprint_ref",
                        "TEAM_BLUEPRINT_NOT_FOUND",
                        "Team blueprint revision is missing.",
                    )
                    continue
                if operation.node_kind == "team":
                    missing_roles = sorted(
                        slot.role_template_ref
                        for slot in state.team_blueprints[team_ref].role_slots
                        if slot.role_template_ref not in state.role_template_refs
                    )
                    if missing_roles:
                        issue(
                            f"{path}.value.team_blueprint_ref",
                            "ROLE_TEMPLATE_NOT_FOUND",
                            "Team blueprint references unavailable role-template revisions.",
                            missing_role_template_refs=missing_roles,
                        )
                        continue
                units[planned_id] = {
                    "id": planned_id,
                    "key": operation.value.stable_key,
                    "kind": operation.node_kind,
                    "parent_id": operation.parent_id,
                    "lifecycle": "planned",
                    "team_ref": team_ref,
                }
                reserved_stable_keys.add(operation.value.stable_key)
                reserved_node_ids.add(planned_id)
                planned_writes.append(f"unit:create:{planned_id}")
                if operation.node_kind == "team":
                    planned_writes.extend((f"team:create:{planned_id}", f"team_link:create:{planned_id}"))
                    blueprint = state.team_blueprints[team_ref]
                    for slot in blueprint.role_slots:
                        slot_id = planned_node_id(
                            organization_id, "role_slot", f"{operation.value.stable_key}:{slot.slot_id}"
                        )
                        slots[slot_id] = {
                            "id": slot_id,
                            "unit_id": planned_id,
                            "key": slot.slot_id,
                            "required": slot.required,
                            "default_count": slot.default_count,
                            "max_count": slot.max_count,
                            "assignment_policy": slot.assignment_policy.model_dump(mode="json"),
                            "sod": slot.separation_of_duties.model_dump(mode="json"),
                            "lifecycle": "active",
                        }
                        reserved_slot_keys.add((planned_id, slot.slot_id))
                        reserved_node_ids.add(slot_id)
                        planned_writes.append(f"role_slot:create:{slot_id}")
            elif isinstance(operation, TopologyRemoveOperation):
                unit = units.get(operation.node_id)
                if unit is not None:
                    subtree = unit_subtree_ids(units, operation.node_id)
                    activity = {
                        key: sum(int(state.activity_by_unit.get(unit_id, {}).get(key, 0)) for unit_id in subtree)
                        for key in {"tasks", "leases", "open_gates", "handoffs", "assignments"}
                    }
                    active = any(value > 0 for value in activity.values())
                    if active and operation.lifecycle_strategy == "archive":
                        issue(
                            path,
                            "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
                            "Active subtree work requires drain or an explicit migration target.",
                            activity=activity,
                        )
                        continue
                    if active:
                        planned_writes.append(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
                    target_lifecycle = "archived"
                    planned_writes.append(f"unit:archive:{operation.node_id}")
                    for unit_id in subtree:
                        units[unit_id]["lifecycle"] = target_lifecycle
                    for relation_id, relation in list(relations.items()):
                        if target_lifecycle == "archived" and subtree & {
                            relation["source_id"],
                            relation["target_id"],
                        }:
                            relations.pop(relation_id)
                            planned_writes.append(f"relation:archive:{relation_id}")
                    continue
                slot = slots.get(operation.node_id)
                if slot is not None:
                    active_assignments = [row for row in assignments if row["slot_id"] == operation.node_id]
                    if active_assignments and operation.lifecycle_strategy == "archive":
                        issue(
                            path,
                            "ORGANIZATION_ROLE_SLOT_DRAIN_REQUIRED",
                            "Active assignments require drain or migrate before archive.",
                        )
                        continue
                    if active_assignments:
                        for assignment in active_assignments:
                            assignments.remove(assignment)
                            agent_id = assignment["agent_id"]
                            global_assignment_counts[agent_id] = max(
                                0,
                                int(global_assignment_counts.get(agent_id, 0)) - 1,
                            )
                            planned_writes.append(f"assignment:end:{assignment['id']}")
                    slots.pop(operation.node_id)
                    planned_writes.append(f"role_slot:{operation.lifecycle_strategy}:{operation.node_id}")
                    continue
                assignment = next((row for row in assignments if row["id"] == operation.node_id), None)
                if assignment:
                    assignments.remove(assignment)
                    agent_id = assignment["agent_id"]
                    global_assignment_counts[agent_id] = max(
                        0,
                        int(global_assignment_counts.get(agent_id, 0)) - 1,
                    )
                    planned_writes.append(f"assignment:end:{operation.node_id}")
                    continue
                relation = relations.pop(operation.node_id, None)
                if relation:
                    planned_writes.append(f"relation:archive:{operation.node_id}")
                    continue
                issue(
                    f"{path}.node_id",
                    "ORGANIZATION_PATCH_NODE_NOT_FOUND",
                    "Remove target is outside the mutable definition topology.",
                )
            elif isinstance(operation, TopologyReparentOperation):
                unit = units.get(operation.node_id)
                parent = units.get(operation.parent_id)
                if unit is None or parent is None:
                    issue(
                        path, "ORGANIZATION_PATCH_REPARENT_NODE_NOT_FOUND", "Reparent endpoints must be active units."
                    )
                    continue
                if unit["kind"] not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
                    issue(path, "ORGANIZATION_PARENT_KIND_INVALID", "Reparent kinds are incompatible.")
                    continue
                subtree = unit_subtree_ids(units, operation.node_id)
                activity = aggregate_subtree_activity(state.activity_by_unit, subtree)
                if any(int(value) > 0 for value in activity.values()) and operation.lifecycle_strategy is None:
                    issue(
                        path,
                        "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
                        "Active subtree work must be drained or migrated by the Hub before reparent can apply.",
                        activity=activity,
                    )
                    continue
                if any(int(value) > 0 for value in activity.values()):
                    planned_writes.append(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
                unit["parent_id"] = operation.parent_id
                planned_writes.append(f"unit:reparent:{operation.node_id}:{operation.parent_id}")
            elif isinstance(operation, TopologyConnectOperation):
                source = units.get(operation.source_id)
                target = units.get(operation.target_id)
                if source is None or target is None:
                    issue(
                        path, "ORGANIZATION_RELATION_DANGLING", "Organization relation endpoints must be active units."
                    )
                    continue
                matrix = RELATION_ENDPOINT_KIND_MATRIX.get(operation.edge_kind)
                if matrix is None or source["kind"] not in matrix[0] or target["kind"] not in matrix[1]:
                    issue(
                        path, "ORGANIZATION_RELATION_ENDPOINT_KIND_INVALID", "Relation endpoint kinds are incompatible."
                    )
                    continue
                if (
                    operation.handoff_contract_ref
                    and operation.handoff_contract_ref not in state.handoff_definition_refs
                ):
                    issue(
                        f"{path}.handoff_contract_ref",
                        "ORGANIZATION_HANDOFF_DEFINITION_NOT_FOUND",
                        "Handoff definition revision is missing or inactive.",
                    )
                    continue
                relation_key = operation.relation_key or f"{operation.edge_kind}:{source['key']}:{target['key']}"
                relation_identity = (operation.edge_kind, operation.source_id, operation.target_id)
                if relation_key in reserved_relation_keys:
                    issue(path, "ORGANIZATION_RELATION_KEY_DUPLICATE", "Relation key already exists.")
                    continue
                if relation_identity in reserved_relation_identities:
                    issue(
                        path,
                        "ORGANIZATION_RELATION_IDENTITY_DUPLICATE",
                        "Relation kind and endpoints already identify an organization relation.",
                    )
                    continue
                relation_id = planned_node_id(organization_id, "relation", relation_key)
                relations[relation_id] = {
                    "id": relation_id,
                    "key": relation_key,
                    "kind": operation.edge_kind,
                    "source_id": operation.source_id,
                    "target_id": operation.target_id,
                    "dependency_policy": operation.dependency_policy,
                }
                reserved_relation_keys.add(relation_key)
                reserved_relation_identities.add(relation_identity)
                planned_writes.append(f"relation:create:{relation_id}")
            else:
                slot = slots.get(operation.role_slot_id)
                agent = state.agents.get(operation.agent_id)
                if slot is None:
                    issue(path, "ORGANIZATION_ROLE_SLOT_NOT_FOUND", "Assignment role slot is missing.")
                    continue
                policy = AssignmentPolicyDefinition.model_validate(slot["assignment_policy"])
                historical = historical_assignments.get((operation.role_slot_id, operation.agent_id))
                capacity_used = int(global_assignment_counts.get(operation.agent_id, 0))
                if historical is not None and historical.lifecycle == "proposed":
                    # A proposed row already reserves one unit of global
                    # capacity; activating it does not consume another.
                    capacity_used = max(0, capacity_used - 1)
                eligibility = self._assignment_eligibility.evaluate(
                    agent=agent,
                    required_capabilities=set(policy.required_capabilities),
                    forbidden_capabilities=set(policy.forbidden_capabilities),
                    capacity_used=capacity_used,
                    principal_kind_allowed="agent" in policy.principal_kinds,
                    write_access_required=policy.write_access_required,
                )
                directory_reasons = {
                    "agent_not_registered",
                    "agent_registration_unvalidated",
                    "agent_not_online",
                }
                if directory_reasons & set(eligibility.reasons):
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_AGENT_INELIGIBLE",
                        "Agent is missing, offline, or not registration-validated.",
                        reasons=list(eligibility.reasons),
                    )
                    continue
                capability_reasons = {
                    reason
                    for reason in eligibility.reasons
                    if reason.startswith(("missing_capability:", "forbidden_capability:"))
                    or reason == "assignment_principal_kind_not_allowed"
                }
                if capability_reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_CAPABILITY_MISMATCH",
                        "Agent capabilities violate the role-slot assignment policy.",
                        reasons=sorted(capability_reasons),
                    )
                    continue
                if "write_access_required" in eligibility.reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_WRITE_ACCESS_REQUIRED",
                        "Role slot requires validated write access.",
                    )
                    continue
                capacity_reasons = {
                    reason
                    for reason in eligibility.reasons
                    if reason in {"agent_capacity_exhausted", "agent_capacity_invalid"}
                }
                if capacity_reasons:
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED",
                        "Agent-global organization assignment capacity is unavailable.",
                        reasons=sorted(capacity_reasons),
                        capacity_used=eligibility.capacity_used,
                        capacity_limit=eligibility.capacity_limit,
                    )
                    continue
                existing_for_slot = [row for row in assignments if row["slot_id"] == operation.role_slot_id]
                if any(row["agent_id"] == operation.agent_id for row in existing_for_slot):
                    issue(path, "ORGANIZATION_ASSIGNMENT_DUPLICATE", "Agent is already assigned to this role slot.")
                    continue
                if slot["max_count"] is not None and len(existing_for_slot) >= int(slot["max_count"]):
                    issue(path, "ORGANIZATION_ROLE_SLOT_CAPACITY_EXCEEDED", "Role-slot assignment maximum is reached.")
                    continue
                separation = evaluate_organization_slot_separation(
                    target=OrganizationSlotSeparationPolicy(
                        slot_id=slot["id"],
                        slot_key=slot["key"],
                        definition=SeparationOfDutiesDefinition.model_validate(slot["sod"]),
                    ),
                    peers=(
                        OrganizationSlotSeparationPolicy(
                            slot_id=row["id"],
                            slot_key=row["key"],
                            definition=SeparationOfDutiesDefinition.model_validate(row["sod"]),
                        )
                        for row in slots.values()
                        if row["unit_id"] == slot["unit_id"]
                    ),
                    assigned_slot_ids=(row["slot_id"] for row in assignments if row["agent_id"] == operation.agent_id),
                    agent_capabilities=eligibility.capabilities,
                )
                if separation.has_conflict and separation.enforcement == "strict":
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_SOD_CONFLICT",
                        "Assignment violates strict separation of duties.",
                        conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
                        external_duties=list(separation.external_duties),
                    )
                    continue
                if separation.has_conflict and separation.enforcement == "warn":
                    issue(
                        path,
                        "ORGANIZATION_ASSIGNMENT_SOD_WARNING",
                        "Assignment has a declared separation-of-duties conflict.",
                        severity="warning",
                        conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
                        external_duties=list(separation.external_duties),
                    )
                assignment_id = (
                    historical.id
                    if historical is not None
                    else planned_node_id(
                        organization_id,
                        "assignment",
                        f"{operation.role_slot_id}:{operation.agent_id}",
                    )
                )
                assignments.append(
                    {"id": assignment_id, "slot_id": operation.role_slot_id, "agent_id": operation.agent_id}
                )
                if historical is None or historical.lifecycle != "proposed":
                    global_assignment_counts[operation.agent_id] = (
                        int(global_assignment_counts.get(operation.agent_id, 0)) + 1
                    )
                action = "reactivate" if historical is not None else "create"
                planned_writes.append(f"assignment:{action}:{assignment_id}")

        if has_parent_cycle(units):
            issue("$.operations", "ORGANIZATION_HIERARCHY_CYCLE", "Patch would introduce a hierarchy cycle.")
        dependency_relations = [row for row in relations.values() if row["dependency_policy"] in {"declared", "gate"}]
        if has_relation_cycle(dependency_relations):
            issue("$.operations", "ORGANIZATION_DEPENDENCY_CYCLE", "Patch would introduce a dependency cycle.")

        active_units = [row for row in units.values() if row["lifecycle"] != "archived"]
        active_team_refs = [row["team_ref"] for row in active_units if row["kind"] == "team"]
        workflow_steps = sum(
            state.workflow_steps.get(state.team_blueprints[ref].workflow_ref, 0)
            for ref in active_team_refs
            if ref in state.team_blueprints
        )
        counts = {
            "team": len(active_team_refs),
            "unit": len(active_units),
            "role_slot": len(slots),
            "assignment": len(assignments),
            "relation": len(relations),
            "workflow_step": workflow_steps,
        }
        checks = (
            (counts["team"], limits.max_team_instances_per_organization, "ORGANIZATION_TEAM_LIMIT_EXCEEDED"),
            (counts["unit"], limits.max_units_per_organization, "ORGANIZATION_UNIT_LIMIT_EXCEEDED"),
            (counts["role_slot"], limits.max_role_slots_per_organization, "ORGANIZATION_ROLE_SLOT_LIMIT_EXCEEDED"),
            (counts["assignment"], limits.max_assignments_per_organization, "ORGANIZATION_ASSIGNMENT_LIMIT_EXCEEDED"),
            (counts["relation"], limits.max_relations_per_organization, "ORGANIZATION_RELATION_LIMIT_EXCEEDED"),
            (
                counts["workflow_step"],
                limits.max_workflow_steps_per_organization,
                "ORGANIZATION_WORKFLOW_STEP_LIMIT_EXCEEDED",
            ),
        )
        for actual, maximum, reason_code in checks:
            if actual > maximum:
                issue(
                    "$.operations",
                    reason_code,
                    "Patch exceeds an effective organization limit.",
                    actual=actual,
                    maximum=maximum,
                )

        expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        diagnostic_payload = [
            {
                "severity": item.severity,
                "reason_code": item.reason_code,
                "message": item.human_message,
                **({"node_ids": item.details.get("node_ids")} if item.details.get("node_ids") else {}),
                **({"activity": item.details.get("activity")} if item.details.get("activity") else {}),
            }
            for item in diagnostics
        ]
        limit_ref = str(state.organization.effective_limit_profile_ref or "")
        if "@" not in limit_ref:
            limit_ref = f"{limit_ref}@{state.organization.effective_limit_profile_revision}"
        payload = {
            "schema_version": "1.0",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "organization_id": organization_id,
            "principal_id": principal_id,
            "expected_revision": document.expected_revision,
            "source_snapshot_hash": source_snapshot_hash,
            "expires_at": expires_at,
            "expires_at_epoch": expires_at_epoch,
            "effective_limit_profile_ref": limit_ref,
            "effective_limit_profile_revision": limits.revision,
            "effective_limit_profile_hash": limits.content_hash(),
            "effective_policy_hash": state.effective_policy_hash,
            "budget_policy_hash": state.budget_policy_hash or "missing",
            # Preserve the request's field-presence contract.  Serializing
            # defaulted ``None`` role-slot fields into structural add
            # operations would make a valid document fail its second model
            # validation when the preview is constructed.
            "operations": [row.model_dump(mode="json", exclude_unset=True) for row in document.operations],
            "planned_writes": sorted(set(planned_writes)),
            "diagnostics": diagnostic_payload,
            "limits": organization_limit_profile_projection(limits),
            "applicable": not any(row.severity == "blocker" for row in diagnostics),
        }
        preview = OrganizationTopologyPatchPreview(
            **payload,
            patch_digest="0" * 64,
        )
        preview = preview.model_copy(update={"patch_digest": canonical_sha256(preview.digest_payload())})
        return TopologyPatchEvaluation(preview=preview, unit_activity=state.activity_by_unit)


__all__ = [
    "OrganizationTopologyPatchEvaluator",
    "TopologyPatchEvaluation",
]
