"""Write-free, ordered interpreter that evaluates a topology patch into a digest-bound preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from agent.models.organization_models import (
    AssignmentPolicyDefinition,
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
from agent.services.organization_topology_patch_draft import TopologyPatchDraft, role_slot_draft_row
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

    def evaluate(
        self, *, state, tenant_id, project_id, organization_id, principal_id, document, limits, expires_at_epoch
    ):
        draft = TopologyPatchDraft.from_state(state)
        source_snapshot_hash = _check_document_preconditions(draft, state=state, document=document, limits=limits)
        for index, operation in enumerate(document.operations):
            self._interpret_operation(
                draft,
                operation=operation,
                path=f"$.operations[{index}]",
                state=state,
                organization_id=organization_id,
            )
        _check_topology_cycles(draft)
        _check_organization_limits(draft, state=state, limits=limits)
        preview = _build_preview(
            draft,
            state=state,
            tenant_id=tenant_id,
            project_id=project_id,
            organization_id=organization_id,
            principal_id=principal_id,
            document=document,
            limits=limits,
            expires_at_epoch=expires_at_epoch,
            source_snapshot_hash=source_snapshot_hash,
        )
        return TopologyPatchEvaluation(preview=preview, unit_activity=state.activity_by_unit)

    def _interpret_operation(self, draft: TopologyPatchDraft, *, operation, path, state, organization_id) -> None:
        if isinstance(operation, TopologyAddOperation):
            _interpret_add(draft, operation=operation, path=path, state=state, organization_id=organization_id)
        elif isinstance(operation, TopologyRemoveOperation):
            _interpret_remove(draft, operation=operation, path=path, state=state)
        elif isinstance(operation, TopologyReparentOperation):
            _interpret_reparent(draft, operation=operation, path=path, state=state)
        elif isinstance(operation, TopologyConnectOperation):
            _interpret_connect(draft, operation=operation, path=path, state=state, organization_id=organization_id)
        else:
            self._interpret_assign(draft, operation=operation, path=path, state=state, organization_id=organization_id)

    def _interpret_assign(self, draft: TopologyPatchDraft, *, operation, path, state, organization_id) -> None:
        slot = draft.slots.get(operation.role_slot_id)
        agent = state.agents.get(operation.agent_id)
        if slot is None:
            draft.issue(path, "ORGANIZATION_ROLE_SLOT_NOT_FOUND", "Assignment role slot is missing.")
            return
        policy = AssignmentPolicyDefinition.model_validate(slot["assignment_policy"])
        historical = draft.historical_assignments.get((operation.role_slot_id, operation.agent_id))
        capacity_used = int(draft.global_assignment_counts.get(operation.agent_id, 0))
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
        if _issue_assignment_eligibility_blocker(draft, path=path, eligibility=eligibility):
            return
        existing_for_slot = [row for row in draft.assignments if row["slot_id"] == operation.role_slot_id]
        if any(row["agent_id"] == operation.agent_id for row in existing_for_slot):
            draft.issue(path, "ORGANIZATION_ASSIGNMENT_DUPLICATE", "Agent is already assigned to this role slot.")
            return
        if slot["max_count"] is not None and len(existing_for_slot) >= int(slot["max_count"]):
            draft.issue(path, "ORGANIZATION_ROLE_SLOT_CAPACITY_EXCEEDED", "Role-slot assignment maximum is reached.")
            return
        if _issue_separation_of_duties(
            draft, path=path, slot=slot, agent_id=operation.agent_id, agent_capabilities=eligibility.capabilities
        ):
            return
        assignment_id = (
            historical.id
            if historical is not None
            else planned_node_id(
                organization_id,
                "assignment",
                f"{operation.role_slot_id}:{operation.agent_id}",
            )
        )
        draft.assignments.append(
            {"id": assignment_id, "slot_id": operation.role_slot_id, "agent_id": operation.agent_id}
        )
        if historical is None or historical.lifecycle != "proposed":
            draft.global_assignment_counts[operation.agent_id] = (
                int(draft.global_assignment_counts.get(operation.agent_id, 0)) + 1
            )
        action = "reactivate" if historical is not None else "create"
        draft.plan(f"assignment:{action}:{assignment_id}")


_DIRECTORY_INELIGIBILITY_REASONS = frozenset(
    {
        "agent_not_registered",
        "agent_registration_unvalidated",
        "agent_not_online",
    }
)
_CAPACITY_INELIGIBILITY_REASONS = frozenset({"agent_capacity_exhausted", "agent_capacity_invalid"})


def _check_document_preconditions(draft: TopologyPatchDraft, *, state, document, limits) -> str:
    """Record document-level blockers and return the bound source snapshot hash."""
    if state.organization.definition_revision != document.expected_revision:
        draft.issue(
            "$.expected_revision", "ORGANIZATION_PATCH_REVISION_STALE", "If-Match definition revision is stale."
        )
    if state.snapshot is None:
        draft.issue("$", "ORGANIZATION_PATCH_SNAPSHOT_MISSING", "Organization has no revision-bound topology snapshot.")
        source_snapshot_hash = "missing"
    else:
        source_snapshot_hash = state.snapshot.snapshot_hash
    if len(document.operations) > limits.max_patch_operations:
        draft.issue(
            "$.operations",
            "ORGANIZATION_PATCH_OPERATION_LIMIT_EXCEEDED",
            "Patch exceeds the effective operation limit.",
        )
    if state.budget_policy_hash is None:
        draft.issue("$", "ORGANIZATION_BUDGET_POLICY_MISSING", "Bound organization budget policy is unavailable.")
    return source_snapshot_hash


def _interpret_add(draft: TopologyPatchDraft, *, operation, path, state, organization_id) -> None:
    parent = draft.units.get(operation.parent_id)
    if parent is None:
        draft.issue(
            f"{path}.parent_id",
            "ORGANIZATION_PATCH_PARENT_NOT_FOUND",
            "Add parent is outside the active topology.",
        )
        return
    planned_id = planned_node_id(organization_id, operation.node_kind, operation.value.stable_key)
    if (
        operation.value.stable_key in draft.reserved_stable_keys
        or planned_id in draft.reserved_node_ids
        or (
            operation.node_kind == "role_slot"
            and (operation.parent_id, str(operation.value.slot_key)) in draft.reserved_slot_keys
        )
    ):
        draft.issue(
            f"{path}.value.stable_key",
            "ORGANIZATION_PATCH_STABLE_KEY_CONFLICT",
            "Stable key already exists in scope.",
        )
        return
    if operation.node_kind == "role_slot":
        _interpret_add_role_slot(
            draft, operation=operation, path=path, state=state, parent=parent, planned_id=planned_id
        )
        return
    _interpret_add_unit(
        draft,
        operation=operation,
        path=path,
        state=state,
        parent=parent,
        planned_id=planned_id,
        organization_id=organization_id,
    )


def _interpret_add_role_slot(draft: TopologyPatchDraft, *, operation, path, state, parent, planned_id) -> None:
    if parent["kind"] != "team":
        draft.issue(
            f"{path}.parent_id",
            "ORGANIZATION_ROLE_SLOT_PARENT_INVALID",
            "Role slots require a team parent.",
        )
        return
    if operation.value.role_template_ref not in state.role_template_refs:
        draft.issue(
            f"{path}.value.role_template_ref",
            "ROLE_TEMPLATE_NOT_FOUND",
            "Role template revision is missing.",
        )
        return
    draft.slots[planned_id] = role_slot_draft_row(
        slot_id=planned_id, unit_id=operation.parent_id, key=operation.value.slot_key, value=operation.value
    )
    draft.reserved_stable_keys.add(operation.value.stable_key)
    draft.reserved_slot_keys.add((operation.parent_id, str(operation.value.slot_key)))
    draft.reserved_node_ids.add(planned_id)
    draft.plan(f"role_slot:create:{planned_id}")


def _interpret_add_unit(
    draft: TopologyPatchDraft, *, operation, path, state, parent, planned_id, organization_id
) -> None:
    if operation.node_kind not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
        draft.issue(
            f"{path}.parent_id",
            "ORGANIZATION_PARENT_KIND_INVALID",
            "Parent and child kinds are incompatible.",
        )
        return
    team_ref = operation.value.team_blueprint_ref
    if operation.node_kind == "team" and not _team_blueprint_is_available(
        draft, path=path, state=state, team_ref=team_ref
    ):
        return
    draft.units[planned_id] = {
        "id": planned_id,
        "key": operation.value.stable_key,
        "kind": operation.node_kind,
        "parent_id": operation.parent_id,
        "lifecycle": "planned",
        "team_ref": team_ref,
    }
    draft.reserved_stable_keys.add(operation.value.stable_key)
    draft.reserved_node_ids.add(planned_id)
    draft.plan(f"unit:create:{planned_id}")
    if operation.node_kind == "team":
        _plan_team_instantiation(
            draft,
            blueprint=state.team_blueprints[team_ref],
            planned_id=planned_id,
            stable_key=operation.value.stable_key,
            organization_id=organization_id,
        )


def _team_blueprint_is_available(draft: TopologyPatchDraft, *, path, state, team_ref) -> bool:
    if team_ref not in state.team_blueprints:
        draft.issue(
            f"{path}.value.team_blueprint_ref",
            "TEAM_BLUEPRINT_NOT_FOUND",
            "Team blueprint revision is missing.",
        )
        return False
    missing_roles = sorted(
        slot.role_template_ref
        for slot in state.team_blueprints[team_ref].role_slots
        if slot.role_template_ref not in state.role_template_refs
    )
    if missing_roles:
        draft.issue(
            f"{path}.value.team_blueprint_ref",
            "ROLE_TEMPLATE_NOT_FOUND",
            "Team blueprint references unavailable role-template revisions.",
            missing_role_template_refs=missing_roles,
        )
        return False
    return True


def _plan_team_instantiation(draft: TopologyPatchDraft, *, blueprint, planned_id, stable_key, organization_id) -> None:
    draft.plan(f"team:create:{planned_id}", f"team_link:create:{planned_id}")
    for slot in blueprint.role_slots:
        slot_id = planned_node_id(organization_id, "role_slot", f"{stable_key}:{slot.slot_id}")
        draft.slots[slot_id] = role_slot_draft_row(slot_id=slot_id, unit_id=planned_id, key=slot.slot_id, value=slot)
        draft.reserved_slot_keys.add((planned_id, slot.slot_id))
        draft.reserved_node_ids.add(slot_id)
        draft.plan(f"role_slot:create:{slot_id}")


def _interpret_remove(draft: TopologyPatchDraft, *, operation, path, state) -> None:
    if operation.node_id in draft.units:
        _interpret_remove_unit(draft, operation=operation, path=path, state=state)
        return
    if operation.node_id in draft.slots:
        _interpret_remove_role_slot(draft, operation=operation, path=path)
        return
    assignment = next((row for row in draft.assignments if row["id"] == operation.node_id), None)
    if assignment:
        draft.end_assignment(assignment)
        draft.plan(f"assignment:end:{operation.node_id}")
        return
    relation = draft.relations.pop(operation.node_id, None)
    if relation:
        draft.plan(f"relation:archive:{operation.node_id}")
        return
    draft.issue(
        f"{path}.node_id",
        "ORGANIZATION_PATCH_NODE_NOT_FOUND",
        "Remove target is outside the mutable definition topology.",
    )


def _interpret_remove_unit(draft: TopologyPatchDraft, *, operation, path, state) -> None:
    subtree = unit_subtree_ids(draft.units, operation.node_id)
    activity = {
        key: sum(int(state.activity_by_unit.get(unit_id, {}).get(key, 0)) for unit_id in subtree)
        for key in {"tasks", "leases", "open_gates", "handoffs", "assignments"}
    }
    active = any(value > 0 for value in activity.values())
    if active and operation.lifecycle_strategy == "archive":
        draft.issue(
            path,
            "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
            "Active subtree work requires drain or an explicit migration target.",
            activity=activity,
        )
        return
    if active:
        draft.plan(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
    draft.plan(f"unit:archive:{operation.node_id}")
    for unit_id in subtree:
        draft.units[unit_id]["lifecycle"] = "archived"
    for relation_id, relation in list(draft.relations.items()):
        if subtree & {relation["source_id"], relation["target_id"]}:
            draft.relations.pop(relation_id)
            draft.plan(f"relation:archive:{relation_id}")


def _interpret_remove_role_slot(draft: TopologyPatchDraft, *, operation, path) -> None:
    active_assignments = [row for row in draft.assignments if row["slot_id"] == operation.node_id]
    if active_assignments and operation.lifecycle_strategy == "archive":
        draft.issue(
            path,
            "ORGANIZATION_ROLE_SLOT_DRAIN_REQUIRED",
            "Active assignments require drain or migrate before archive.",
        )
        return
    for assignment in active_assignments:
        draft.end_assignment(assignment)
        draft.plan(f"assignment:end:{assignment['id']}")
    draft.slots.pop(operation.node_id)
    draft.plan(f"role_slot:{operation.lifecycle_strategy}:{operation.node_id}")


def _interpret_reparent(draft: TopologyPatchDraft, *, operation, path, state) -> None:
    unit = draft.units.get(operation.node_id)
    parent = draft.units.get(operation.parent_id)
    if unit is None or parent is None:
        draft.issue(path, "ORGANIZATION_PATCH_REPARENT_NODE_NOT_FOUND", "Reparent endpoints must be active units.")
        return
    if unit["kind"] not in PARENT_KIND_MATRIX.get(parent["kind"], set()):
        draft.issue(path, "ORGANIZATION_PARENT_KIND_INVALID", "Reparent kinds are incompatible.")
        return
    subtree = unit_subtree_ids(draft.units, operation.node_id)
    activity = aggregate_subtree_activity(state.activity_by_unit, subtree)
    active = any(int(value) > 0 for value in activity.values())
    if active and operation.lifecycle_strategy is None:
        draft.issue(
            path,
            "ORGANIZATION_ACTIVE_WORK_STRATEGY_REQUIRED",
            "Active subtree work must be drained or migrated by the Hub before reparent can apply.",
            activity=activity,
        )
        return
    if active:
        draft.plan(f"active_work:{operation.lifecycle_strategy}:{operation.node_id}")
    unit["parent_id"] = operation.parent_id
    draft.plan(f"unit:reparent:{operation.node_id}:{operation.parent_id}")


def _interpret_connect(draft: TopologyPatchDraft, *, operation, path, state, organization_id) -> None:
    source = draft.units.get(operation.source_id)
    target = draft.units.get(operation.target_id)
    if source is None or target is None:
        draft.issue(path, "ORGANIZATION_RELATION_DANGLING", "Organization relation endpoints must be active units.")
        return
    matrix = RELATION_ENDPOINT_KIND_MATRIX.get(operation.edge_kind)
    if matrix is None or source["kind"] not in matrix[0] or target["kind"] not in matrix[1]:
        draft.issue(path, "ORGANIZATION_RELATION_ENDPOINT_KIND_INVALID", "Relation endpoint kinds are incompatible.")
        return
    if operation.handoff_contract_ref and operation.handoff_contract_ref not in state.handoff_definition_refs:
        draft.issue(
            f"{path}.handoff_contract_ref",
            "ORGANIZATION_HANDOFF_DEFINITION_NOT_FOUND",
            "Handoff definition revision is missing or inactive.",
        )
        return
    relation_key = operation.relation_key or f"{operation.edge_kind}:{source['key']}:{target['key']}"
    relation_identity = (operation.edge_kind, operation.source_id, operation.target_id)
    if relation_key in draft.reserved_relation_keys:
        draft.issue(path, "ORGANIZATION_RELATION_KEY_DUPLICATE", "Relation key already exists.")
        return
    if relation_identity in draft.reserved_relation_identities:
        draft.issue(
            path,
            "ORGANIZATION_RELATION_IDENTITY_DUPLICATE",
            "Relation kind and endpoints already identify an organization relation.",
        )
        return
    relation_id = planned_node_id(organization_id, "relation", relation_key)
    draft.relations[relation_id] = {
        "id": relation_id,
        "key": relation_key,
        "kind": operation.edge_kind,
        "source_id": operation.source_id,
        "target_id": operation.target_id,
        "dependency_policy": operation.dependency_policy,
    }
    draft.reserved_relation_keys.add(relation_key)
    draft.reserved_relation_identities.add(relation_identity)
    draft.plan(f"relation:create:{relation_id}")


def _issue_assignment_eligibility_blocker(draft: TopologyPatchDraft, *, path, eligibility) -> bool:
    """Record the first eligibility blocker in precedence order; return whether one was recorded."""
    if _DIRECTORY_INELIGIBILITY_REASONS & set(eligibility.reasons):
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_AGENT_INELIGIBLE",
            "Agent is missing, offline, or not registration-validated.",
            reasons=list(eligibility.reasons),
        )
        return True
    capability_reasons = {
        reason
        for reason in eligibility.reasons
        if reason.startswith(("missing_capability:", "forbidden_capability:"))
        or reason == "assignment_principal_kind_not_allowed"
    }
    if capability_reasons:
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_CAPABILITY_MISMATCH",
            "Agent capabilities violate the role-slot assignment policy.",
            reasons=sorted(capability_reasons),
        )
        return True
    if "write_access_required" in eligibility.reasons:
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_WRITE_ACCESS_REQUIRED",
            "Role slot requires validated write access.",
        )
        return True
    capacity_reasons = {reason for reason in eligibility.reasons if reason in _CAPACITY_INELIGIBILITY_REASONS}
    if capacity_reasons:
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_AGENT_CAPACITY_EXHAUSTED",
            "Agent-global organization assignment capacity is unavailable.",
            reasons=sorted(capacity_reasons),
            capacity_used=eligibility.capacity_used,
            capacity_limit=eligibility.capacity_limit,
        )
        return True
    return False


def _issue_separation_of_duties(draft: TopologyPatchDraft, *, path, slot, agent_id, agent_capabilities) -> bool:
    """Record SoD diagnostics; return whether a strict conflict blocks the assignment."""
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
            for row in draft.slots.values()
            if row["unit_id"] == slot["unit_id"]
        ),
        assigned_slot_ids=(row["slot_id"] for row in draft.assignments if row["agent_id"] == agent_id),
        agent_capabilities=agent_capabilities,
    )
    if separation.has_conflict and separation.enforcement == "strict":
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_SOD_CONFLICT",
            "Assignment violates strict separation of duties.",
            conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
            external_duties=list(separation.external_duties),
        )
        return True
    if separation.has_conflict and separation.enforcement == "warn":
        draft.issue(
            path,
            "ORGANIZATION_ASSIGNMENT_SOD_WARNING",
            "Assignment has a declared separation-of-duties conflict.",
            severity="warning",
            conflicting_role_slot_ids=list(separation.conflicting_slot_ids),
            external_duties=list(separation.external_duties),
        )
    return False


def _check_topology_cycles(draft: TopologyPatchDraft) -> None:
    if has_parent_cycle(draft.units):
        draft.issue("$.operations", "ORGANIZATION_HIERARCHY_CYCLE", "Patch would introduce a hierarchy cycle.")
    dependency_relations = [
        row for row in draft.relations.values() if row["dependency_policy"] in {"declared", "gate"}
    ]
    if has_relation_cycle(dependency_relations):
        draft.issue("$.operations", "ORGANIZATION_DEPENDENCY_CYCLE", "Patch would introduce a dependency cycle.")


def _check_organization_limits(draft: TopologyPatchDraft, *, state, limits) -> None:
    active_units = [row for row in draft.units.values() if row["lifecycle"] != "archived"]
    active_team_refs = [row["team_ref"] for row in active_units if row["kind"] == "team"]
    workflow_steps = sum(
        state.workflow_steps.get(state.team_blueprints[ref].workflow_ref, 0)
        for ref in active_team_refs
        if ref in state.team_blueprints
    )
    checks = (
        (len(active_team_refs), limits.max_team_instances_per_organization, "ORGANIZATION_TEAM_LIMIT_EXCEEDED"),
        (len(active_units), limits.max_units_per_organization, "ORGANIZATION_UNIT_LIMIT_EXCEEDED"),
        (len(draft.slots), limits.max_role_slots_per_organization, "ORGANIZATION_ROLE_SLOT_LIMIT_EXCEEDED"),
        (len(draft.assignments), limits.max_assignments_per_organization, "ORGANIZATION_ASSIGNMENT_LIMIT_EXCEEDED"),
        (len(draft.relations), limits.max_relations_per_organization, "ORGANIZATION_RELATION_LIMIT_EXCEEDED"),
        (workflow_steps, limits.max_workflow_steps_per_organization, "ORGANIZATION_WORKFLOW_STEP_LIMIT_EXCEEDED"),
    )
    for actual, maximum, reason_code in checks:
        if actual > maximum:
            draft.issue(
                "$.operations",
                reason_code,
                "Patch exceeds an effective organization limit.",
                actual=actual,
                maximum=maximum,
            )


def _build_preview(
    draft: TopologyPatchDraft,
    *,
    state,
    tenant_id,
    project_id,
    organization_id,
    principal_id,
    document,
    limits,
    expires_at_epoch,
    source_snapshot_hash,
) -> OrganizationTopologyPatchPreview:
    expires_at = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    diagnostic_payload = [
        {
            "severity": item.severity,
            "reason_code": item.reason_code,
            "message": item.human_message,
            **({"node_ids": item.details.get("node_ids")} if item.details.get("node_ids") else {}),
            **({"activity": item.details.get("activity")} if item.details.get("activity") else {}),
        }
        for item in draft.diagnostics
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
        "planned_writes": sorted(set(draft.planned_writes)),
        "diagnostics": diagnostic_payload,
        "limits": organization_limit_profile_projection(limits),
        "applicable": not draft.has_blocker,
    }
    preview = OrganizationTopologyPatchPreview(
        **payload,
        patch_digest="0" * 64,
    )
    return preview.model_copy(update={"patch_digest": canonical_sha256(preview.digest_payload())})


__all__ = [
    "OrganizationTopologyPatchEvaluator",
    "TopologyPatchEvaluation",
]
