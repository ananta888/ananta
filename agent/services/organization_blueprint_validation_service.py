"""Semantic validation for portable organization definitions.

Schema validation answers whether JSON has the right shape.  This service
answers whether its references, hierarchy and bounded graph are meaningful.
It is intentionally write-free and depends only on catalog read ports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from agent.models.organization_models import (
    OrganizationBlueprintDefinition,
    OrganizationDiagnostic,
    OrganizationLimitProfile,
    VersionedDefinitionRef,
)
from agent.ports.organization_definitions import OrganizationDefinitionCatalogPort

DiagnosticSink = Callable[..., None]

PARENT_KIND_MATRIX: dict[str, set[str]] = {
    "coordination_unit": {"coordination_unit", "value_stream", "team"},
    "value_stream": {"value_stream", "team"},
    "team": set(),
}

RELATION_ENDPOINT_KIND_MATRIX: dict[str, tuple[set[str], set[str]]] = {
    "governs": ({"coordination_unit", "team"}, {"coordination_unit", "value_stream", "team"}),
    "enables": ({"value_stream", "team"}, {"value_stream", "team"}),
    "supplies_research_to": ({"team"}, {"value_stream", "team"}),
    "prototypes_for": ({"team"}, {"value_stream", "team"}),
    "reviews": ({"team"}, {"value_stream", "team"}),
    "releases_for": ({"team"}, {"value_stream", "team"}),
    "declared_dependency": (
        {"coordination_unit", "value_stream", "team"},
        {"coordination_unit", "value_stream", "team"},
    ),
    "handoff": ({"team"}, {"team"}),
    "escalates_to": (
        {"coordination_unit", "value_stream", "team"},
        {"coordination_unit", "team"},
    ),
}


class OrganizationBlueprintValidationError(ValueError):
    def __init__(self, issues: list[OrganizationDiagnostic]) -> None:
        self.issues = issues
        super().__init__(issues[0].reason_code if issues else "organization_blueprint_invalid")


class OrganizationBlueprintValidationService:
    def validate(
        self,
        definition: OrganizationBlueprintDefinition,
        *,
        catalog: OrganizationDefinitionCatalogPort,
        limits: OrganizationLimitProfile,
    ) -> list[OrganizationDiagnostic]:
        issues: list[OrganizationDiagnostic] = []

        def blocker(path: str, reason_code: str, message: str, **details: object) -> None:
            issues.append(
                OrganizationDiagnostic(
                    path=path,
                    reason_code=reason_code,
                    human_message=message,
                    severity="blocker",
                    details=dict(details),
                )
            )

        units_by_key = self._index_units(definition, blocker)
        groups_by_key, validated_team_refs = self._validate_unit_groups(
            definition=definition,
            units_by_key=units_by_key,
            catalog=catalog,
            limits=limits,
            blocker=blocker,
        )

        self._validate_unit_hierarchy(
            definition=definition,
            units_by_key=units_by_key,
            validated_team_refs=validated_team_refs,
            catalog=catalog,
            blocker=blocker,
        )

        self._validate_relations(
            definition=definition,
            units_by_key=units_by_key,
            catalog=catalog,
            blocker=blocker,
        )

        self._validate_standard_and_limits(
            definition=definition,
            units_by_key=units_by_key,
            groups_by_key=groups_by_key,
            catalog=catalog,
            limits=limits,
            blocker=blocker,
        )
        return issues

    @staticmethod
    def _index_units(
        definition: OrganizationBlueprintDefinition,
        blocker: DiagnosticSink,
    ) -> dict[str, Any]:
        units_by_key: dict[str, Any] = {}
        for index, unit in enumerate(definition.units):
            path = f"$.organization_blueprints[{definition.key!r}].units[{index}]"
            if unit.unit_key in units_by_key:
                blocker(
                    f"{path}.unit_key",
                    "ORGANIZATION_UNIT_KEY_DUPLICATE",
                    "Unit key is not unique.",
                )
                continue
            units_by_key[unit.unit_key] = unit
        return units_by_key

    def _validate_unit_groups(
        self,
        *,
        definition: OrganizationBlueprintDefinition,
        units_by_key: dict[str, Any],
        catalog: OrganizationDefinitionCatalogPort,
        limits: OrganizationLimitProfile,
        blocker: DiagnosticSink,
    ) -> tuple[dict[str, Any], set[str]]:
        groups_by_key: dict[str, Any] = {}
        validated_team_refs: set[str] = set()
        fixed_team_count = sum(unit.materialization_kind == "team_instance" for unit in definition.units)
        effective_group_max = limits.max_team_instances_per_organization - fixed_team_count
        for index, group in enumerate(definition.unit_groups):
            path = f"$.organization_blueprints[{definition.key!r}].unit_groups[{index}]"
            if group.group_id in groups_by_key or group.group_id in units_by_key:
                blocker(
                    f"{path}.group_id",
                    "ORGANIZATION_GROUP_KEY_DUPLICATE",
                    "Group key is not unique.",
                )
            groups_by_key[group.group_id] = group
            if group.parent_unit_ref not in units_by_key:
                blocker(
                    f"{path}.parent_unit_ref",
                    "ORGANIZATION_PARENT_UNIT_NOT_FOUND",
                    "Group parent unit does not exist.",
                    parent_unit_ref=group.parent_unit_ref,
                )
            if group.team_blueprint_ref not in validated_team_refs:
                validated_team_refs.add(group.team_blueprint_ref)
                self._validate_team_blueprint_ref(
                    group.team_blueprint_ref,
                    f"{path}.team_blueprint_ref",
                    catalog,
                    blocker,
                )
            if not catalog.has_policy(group.limit_policy_ref):
                blocker(
                    f"{path}.limit_policy_ref",
                    "ORGANIZATION_LIMIT_POLICY_NOT_FOUND",
                    "Unit-group limit policy is missing.",
                )
            if effective_group_max < group.capacity_rule.minimum_remaining:
                blocker(
                    f"{path}.capacity_rule.minimum_remaining",
                    "ORGANIZATION_GROUP_CAPACITY_EXHAUSTED",
                    "No capacity remains for the declared group minimum.",
                    effective_max=effective_group_max,
                )
            if group.max_count is not None and group.max_count > effective_group_max:
                blocker(
                    f"{path}.max_count",
                    "ORGANIZATION_GROUP_LIMIT_EXCEEDED",
                    "Declared group maximum exceeds remaining team capacity.",
                    effective_max=effective_group_max,
                )
        return groups_by_key, validated_team_refs

    def _validate_unit_hierarchy(
        self,
        *,
        definition: OrganizationBlueprintDefinition,
        units_by_key: dict[str, Any],
        validated_team_refs: set[str],
        catalog: OrganizationDefinitionCatalogPort,
        blocker: DiagnosticSink,
    ) -> None:
        for index, unit in enumerate(definition.units):
            path = f"$.organization_blueprints[{definition.key!r}].units[{index}]"
            parent = units_by_key.get(unit.parent_unit_ref) if unit.parent_unit_ref else None
            if unit.parent_unit_ref and parent is None:
                blocker(
                    f"{path}.parent_unit_ref",
                    "ORGANIZATION_PARENT_UNIT_NOT_FOUND",
                    "Parent unit does not exist.",
                    parent_unit_ref=unit.parent_unit_ref,
                )
            elif parent is not None and unit.unit_kind not in PARENT_KIND_MATRIX.get(parent.unit_kind, set()):
                blocker(
                    f"{path}.parent_unit_ref",
                    "ORGANIZATION_PARENT_KIND_INVALID",
                    "Parent and child unit kinds are incompatible.",
                    parent_kind=parent.unit_kind,
                    child_kind=unit.unit_kind,
                )
            if unit.team_blueprint_ref and unit.team_blueprint_ref not in validated_team_refs:
                validated_team_refs.add(unit.team_blueprint_ref)
                self._validate_team_blueprint_ref(
                    unit.team_blueprint_ref,
                    f"{path}.team_blueprint_ref",
                    catalog,
                    blocker,
                )

        cycle = _first_cycle({key: unit.parent_unit_ref for key, unit in units_by_key.items()})
        if cycle:
            blocker(
                "$.organization_blueprints[*].units[*].parent_unit_ref",
                "ORGANIZATION_HIERARCHY_CYCLE",
                "The unit hierarchy contains a cycle.",
                cycle=cycle,
            )

    @staticmethod
    def _validate_relations(
        *,
        definition: OrganizationBlueprintDefinition,
        units_by_key: dict[str, Any],
        catalog: OrganizationDefinitionCatalogPort,
        blocker: DiagnosticSink,
    ) -> None:
        seen_relations: set[str] = set()
        dependency_graph: dict[str, set[str]] = defaultdict(set)
        for index, relation in enumerate(definition.relations):
            path = f"$.organization_blueprints[{definition.key!r}].relations[{index}]"
            if relation.relation_id in seen_relations:
                blocker(
                    f"{path}.relation_id",
                    "ORGANIZATION_RELATION_KEY_DUPLICATE",
                    "Relation key is not unique.",
                )
            seen_relations.add(relation.relation_id)
            if relation.source_unit_ref == relation.target_unit_ref:
                blocker(
                    path,
                    "ORGANIZATION_RELATION_SELF_REFERENCE",
                    "Relation endpoints must be distinct.",
                )
            source = units_by_key.get(relation.source_unit_ref)
            target = units_by_key.get(relation.target_unit_ref)
            if source is None:
                blocker(
                    f"{path}.source_unit_ref",
                    "ORGANIZATION_RELATION_SOURCE_NOT_FOUND",
                    "Relation source does not exist.",
                )
            if target is None:
                blocker(
                    f"{path}.target_unit_ref",
                    "ORGANIZATION_RELATION_TARGET_NOT_FOUND",
                    "Relation target does not exist.",
                )
            matrix = RELATION_ENDPOINT_KIND_MATRIX.get(relation.kind)
            if matrix is None:
                blocker(
                    f"{path}.kind",
                    "ORGANIZATION_RELATION_KIND_UNKNOWN",
                    "Relation kind is not supported.",
                )
            elif (
                source is not None
                and target is not None
                and (source.unit_kind not in matrix[0] or target.unit_kind not in matrix[1])
            ):
                blocker(
                    path,
                    "ORGANIZATION_RELATION_ENDPOINT_KIND_INVALID",
                    "Relation endpoint kinds are incompatible.",
                    source_kind=source.unit_kind,
                    target_kind=target.unit_kind,
                )
            OrganizationBlueprintValidationService._validate_handoff_reference(
                relation=relation,
                path=path,
                catalog=catalog,
                blocker=blocker,
            )
            if relation.dependency_policy in {"declared", "gate"} and source is not None and target is not None:
                dependency_graph[relation.source_unit_ref].add(relation.target_unit_ref)
                dependency_graph.setdefault(relation.target_unit_ref, set())

        dependency_cycle = _first_directed_cycle(dependency_graph)
        if dependency_cycle:
            blocker(
                "$.organization_blueprints[*].relations",
                "ORGANIZATION_DEPENDENCY_CYCLE",
                "Declared organization dependencies contain a cycle.",
                cycle=dependency_cycle,
            )

    @staticmethod
    def _validate_handoff_reference(
        *,
        relation: Any,
        path: str,
        catalog: OrganizationDefinitionCatalogPort,
        blocker: DiagnosticSink,
    ) -> None:
        if not relation.handoff_contract_ref:
            return
        try:
            ref = VersionedDefinitionRef.parse(relation.handoff_contract_ref)
        except ValueError:
            blocker(
                f"{path}.handoff_contract_ref",
                "HANDOFF_DEFINITION_REF_INVALID",
                "Handoff reference is invalid.",
            )
            return
        if not catalog.has_handoff_definition(ref.key, ref.version):
            blocker(
                f"{path}.handoff_contract_ref",
                "HANDOFF_DEFINITION_NOT_FOUND",
                "Handoff definition is missing.",
            )

    @staticmethod
    def _validate_standard_and_limits(
        *,
        definition: OrganizationBlueprintDefinition,
        units_by_key: dict[str, Any],
        groups_by_key: dict[str, Any],
        catalog: OrganizationDefinitionCatalogPort,
        limits: OrganizationLimitProfile,
        blocker: DiagnosticSink,
    ) -> None:
        standard = definition.standard_composition
        if len(set(standard.baseline_singleton_team_refs)) != len(standard.baseline_singleton_team_refs):
            blocker(
                "$.organization_blueprints[*].standard_composition.baseline_singleton_team_refs",
                "STANDARD_SINGLETON_TEAM_DUPLICATE",
                "Baseline singleton references must be unique.",
            )
        if len(set(standard.activation_order)) != len(standard.activation_order):
            blocker(
                "$.organization_blueprints[*].standard_composition.activation_order",
                "STANDARD_ACTIVATION_TEAM_DUPLICATE",
                "Activation-order references must be unique.",
            )
        for key in standard.baseline_singleton_team_refs:
            unit = units_by_key.get(key)
            if unit is None or unit.materialization_kind != "team_instance":
                blocker(
                    "$.organization_blueprints[*].standard_composition.baseline_singleton_team_refs",
                    "STANDARD_SINGLETON_TEAM_NOT_FOUND",
                    "Baseline singleton reference is not a team unit.",
                    unit_key=key,
                )
        for key in standard.activation_order:
            unit = units_by_key.get(key)
            if unit is None or unit.activation_policy != "ordered_optional":
                blocker(
                    "$.organization_blueprints[*].standard_composition.activation_order",
                    "STANDARD_ACTIVATION_TEAM_INVALID",
                    "Activation-order reference is not an optional team unit.",
                    unit_key=key,
                )
        if standard.scale_out_group not in groups_by_key:
            blocker(
                "$.organization_blueprints[*].standard_composition.scale_out_group",
                "STANDARD_SCALE_OUT_GROUP_NOT_FOUND",
                "Scale-out group is missing.",
            )
        for group_key, count in standard.baseline_group_counts.items():
            group = groups_by_key.get(group_key)
            if group is None or count < group.min_count or (group.max_count is not None and count > group.max_count):
                blocker(
                    f"$.organization_blueprints[*].standard_composition.baseline_group_counts.{group_key}",
                    "STANDARD_GROUP_COUNT_INVALID",
                    "Baseline group count is outside its declared cardinality.",
                )
        baseline_count = len(set(standard.baseline_singleton_team_refs)) + sum(standard.baseline_group_counts.values())
        if baseline_count != standard.minimum:
            blocker(
                "$.organization_blueprints[*].standard_composition",
                "STANDARD_BASELINE_COUNT_MISMATCH",
                "Baseline composition does not match its minimum.",
                declared_minimum=standard.minimum,
                derived_baseline_count=baseline_count,
            )
        if standard.maximum > limits.max_team_instances_per_organization:
            blocker(
                "$.organization_blueprints[*].standard_composition.maximum",
                "ORGANIZATION_TEAM_LIMIT_EXCEEDED",
                "Standard band exceeds the effective team limit.",
            )
        if len(definition.units) > limits.max_units_per_organization:
            blocker(
                "$.organization_blueprints[*].units",
                "ORGANIZATION_UNIT_LIMIT_EXCEEDED",
                "Definition exceeds the unit limit.",
            )
        if len(definition.relations) > limits.max_relations_per_organization:
            blocker(
                "$.organization_blueprints[*].relations",
                "ORGANIZATION_RELATION_LIMIT_EXCEEDED",
                "Definition exceeds the relation limit.",
            )
        if not catalog.has_policy(definition.limit_policy_ref):
            blocker(
                "$.organization_blueprints[*].limit_policy_ref",
                "ORGANIZATION_LIMIT_POLICY_NOT_FOUND",
                "Limit policy is missing.",
            )
        if not catalog.has_policy(definition.budgets.policy_ref):
            blocker(
                "$.organization_blueprints[*].budgets.policy_ref",
                "ORGANIZATION_BUDGET_POLICY_NOT_FOUND",
                "Budget policy is missing.",
            )
        if definition.orchestration.owner != "hub" or definition.orchestration.workers_may_orchestrate is not False:
            blocker(
                "$.organization_blueprints[*].orchestration",
                "ORGANIZATION_HUB_OWNERSHIP_REQUIRED",
                "The Hub must remain the sole orchestration owner.",
            )

    def ensure_valid(
        self,
        definition: OrganizationBlueprintDefinition,
        *,
        catalog: OrganizationDefinitionCatalogPort,
        limits: OrganizationLimitProfile,
    ) -> None:
        issues = self.validate(definition, catalog=catalog, limits=limits)
        if issues:
            raise OrganizationBlueprintValidationError(issues)

    @staticmethod
    def _validate_team_blueprint_ref(ref_value, path, catalog, blocker) -> None:
        try:
            ref = VersionedDefinitionRef.parse(ref_value)
        except ValueError:
            blocker(path, "TEAM_BLUEPRINT_REF_INVALID", "Team blueprint reference is invalid.")
            return
        team = catalog.get_team_blueprint(ref.key, ref.version)
        if team is None:
            blocker(path, "TEAM_BLUEPRINT_NOT_FOUND", "Team blueprint is missing.", definition_ref=ref_value)
            return
        known_slots = {item.slot_id for item in team.role_slots}
        if len(known_slots) != len(team.role_slots):
            blocker(
                f"{path}.role_slots",
                "ROLE_SLOT_KEY_DUPLICATE",
                "Role-slot keys must be unique within a team blueprint.",
            )
        artifact_kinds = [item.kind for item in team.artifacts]
        if len(set(artifact_kinds)) != len(artifact_kinds):
            blocker(
                f"{path}.artifacts",
                "TEAM_ARTIFACT_KIND_DUPLICATE",
                "Artifact contract kinds must be unique within a team blueprint.",
            )
        for slot_index, slot in enumerate(team.role_slots):
            role_ref = VersionedDefinitionRef.parse(slot.role_template_ref)
            if not catalog.has_role_template(role_ref.key, role_ref.version):
                blocker(
                    f"{path}.role_slots[{slot_index}].role_template_ref",
                    "ROLE_TEMPLATE_NOT_FOUND",
                    "Role template is missing.",
                    definition_ref=slot.role_template_ref,
                )
            unknown_independence_refs = set(slot.separation_of_duties.independent_from_slot_ids) - known_slots
            if unknown_independence_refs:
                blocker(
                    f"{path}.role_slots[{slot_index}].separation_of_duties.independent_from_slot_ids",
                    "ROLE_SLOT_SEPARATION_REFERENCE_NOT_FOUND",
                    "Separation-of-duties references an unknown role slot.",
                    slot_ids=sorted(unknown_independence_refs),
                )
            for overlay_index, overlay_ref in enumerate(slot.overlays):
                if not catalog.has_policy(overlay_ref):
                    blocker(
                        f"{path}.role_slots[{slot_index}].overlays[{overlay_index}]",
                        "ROLE_SLOT_OVERLAY_NOT_FOUND",
                        "Role-slot overlay definition is missing.",
                    )
        for policy_index, policy_ref in enumerate(team.policies):
            if not catalog.has_policy(policy_ref):
                blocker(
                    f"{path}.policies[{policy_index}]",
                    "TEAM_POLICY_NOT_FOUND",
                    "Team policy definition is missing.",
                )
        try:
            workflow_ref = VersionedDefinitionRef.parse(team.workflow_ref)
        except ValueError:
            blocker(f"{path}.workflow_ref", "WORKFLOW_DEFINITION_REF_INVALID", "Workflow reference is invalid.")
            return
        workflow = catalog.get_workflow_definition(workflow_ref.key, workflow_ref.version)
        if workflow is None:
            blocker(f"{path}.workflow_ref", "WORKFLOW_DEFINITION_NOT_FOUND", "Workflow definition is missing.")
            return
        OrganizationBlueprintValidationService._validate_workflow_definition(
            workflow,
            path=f"{path}.workflow_ref",
            catalog=catalog,
            blocker=blocker,
        )

    @staticmethod
    def _validate_workflow_definition(workflow, *, path, catalog, blocker) -> None:
        steps = list(workflow.get("steps") or [])
        step_ids = [str(step.get("step_id") or "") for step in steps]
        if any(not step_id for step_id in step_ids):
            blocker(f"{path}.steps", "WORKFLOW_STEP_ID_MISSING", "Every workflow step needs a stable step_id.")
        if len(set(step_ids)) != len(step_ids):
            blocker(f"{path}.steps", "WORKFLOW_STEP_ID_DUPLICATE", "Workflow step IDs must be unique.")
        known_steps = set(step_ids)
        dependency_graph: dict[str, set[str]] = defaultdict(set)
        for index, step in enumerate(steps):
            step_path = f"{path}.steps[{index}]"
            step_id = str(step.get("step_id") or "")
            dependencies = {str(value) for value in step.get("depends_on") or []}
            unknown = dependencies - known_steps
            if unknown:
                blocker(
                    f"{step_path}.depends_on",
                    "WORKFLOW_STEP_DEPENDENCY_NOT_FOUND",
                    "Workflow step references an unknown dependency.",
                    step_ids=sorted(unknown),
                )
            dependency_graph.setdefault(step_id, set()).update(dependencies & known_steps)

            for field in ("owner_role_ref",):
                try:
                    ref = VersionedDefinitionRef.parse(str(step.get(field) or ""))
                except ValueError:
                    blocker(f"{step_path}.{field}", "WORKFLOW_ROLE_REF_INVALID", "Workflow role reference is invalid.")
                else:
                    if not catalog.has_role_template(ref.key, ref.version):
                        blocker(f"{step_path}.{field}", "WORKFLOW_ROLE_NOT_FOUND", "Workflow role template is missing.")

            approval_ref_value = (step.get("gate") or {}).get("approval_role_ref")
            if approval_ref_value:
                try:
                    approval_ref = VersionedDefinitionRef.parse(str(approval_ref_value))
                except ValueError:
                    blocker(
                        f"{step_path}.gate.approval_role_ref",
                        "WORKFLOW_APPROVAL_ROLE_REF_INVALID",
                        "Workflow approval-role reference is invalid.",
                    )
                else:
                    if not catalog.has_role_template(approval_ref.key, approval_ref.version):
                        blocker(
                            f"{step_path}.gate.approval_role_ref",
                            "WORKFLOW_APPROVAL_ROLE_NOT_FOUND",
                            "Workflow approval role template is missing.",
                        )

            selector_ref_value = (step.get("target_team_selector") or {}).get("team_blueprint_ref")
            try:
                selector_ref = VersionedDefinitionRef.parse(str(selector_ref_value or ""))
            except ValueError:
                blocker(
                    f"{step_path}.target_team_selector.team_blueprint_ref",
                    "WORKFLOW_TEAM_BLUEPRINT_REF_INVALID",
                    "Workflow target team reference is invalid.",
                )
            else:
                if catalog.get_team_blueprint(selector_ref.key, selector_ref.version) is None:
                    blocker(
                        f"{step_path}.target_team_selector.team_blueprint_ref",
                        "WORKFLOW_TEAM_BLUEPRINT_NOT_FOUND",
                        "Workflow target team blueprint is missing.",
                    )

            handoff_ref_value = step.get("handoff_ref")
            if handoff_ref_value:
                try:
                    handoff_ref = VersionedDefinitionRef.parse(str(handoff_ref_value))
                except ValueError:
                    blocker(
                        f"{step_path}.handoff_ref",
                        "WORKFLOW_HANDOFF_REF_INVALID",
                        "Workflow handoff reference is invalid.",
                    )
                else:
                    if not catalog.has_handoff_definition(handoff_ref.key, handoff_ref.version):
                        blocker(
                            f"{step_path}.handoff_ref",
                            "WORKFLOW_HANDOFF_NOT_FOUND",
                            "Workflow handoff definition is missing.",
                        )
                    else:
                        handoff = catalog.get_handoff_definition(handoff_ref.key, handoff_ref.version)
                        # Existing handoff contracts predate strict producer
                        # declarations.  New definitions opt in explicitly so
                        # validation can harden additively without changing a
                        # published @1 workflow's canonical content.
                        if (handoff or {}).get("artifact_source") == "workflow_step_outputs":
                            required_artifacts = {
                                str(value) for value in list((handoff or {}).get("required_artifact_kinds") or [])
                            }
                            produced_artifacts = {str(value) for value in list(step.get("outputs") or [])}
                            missing_artifacts = sorted(required_artifacts - produced_artifacts)
                            if missing_artifacts:
                                blocker(
                                    f"{step_path}.handoff_ref",
                                    "WORKFLOW_HANDOFF_ARTIFACT_NOT_PRODUCED",
                                    "Workflow step does not produce every artifact required by its handoff.",
                                    artifact_kinds=missing_artifacts,
                                )

        cycle = _first_directed_cycle(dependency_graph)
        if cycle:
            blocker(
                f"{path}.steps",
                "WORKFLOW_DEPENDENCY_CYCLE_UNBOUNDED",
                "Workflow dependency cycles require an explicit bounded-loop policy.",
                cycle=cycle,
            )


def _first_cycle(parent_by_node: dict[str, str | None]) -> list[str] | None:
    """Return one deterministic cycle from a single-parent directed graph."""

    state: dict[str, int] = defaultdict(int)
    stack: list[str] = []
    stack_position: dict[str, int] = {}

    def visit(node: str) -> list[str] | None:
        if state[node] == 2:
            return None
        if state[node] == 1:
            start = stack_position[node]
            return [*stack[start:], node]
        state[node] = 1
        stack_position[node] = len(stack)
        stack.append(node)
        target = parent_by_node.get(node)
        result = visit(target) if target in parent_by_node else None
        stack.pop()
        stack_position.pop(node, None)
        state[node] = 2
        return result

    for node in sorted(parent_by_node):
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def _first_directed_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = defaultdict(int)
    stack: list[str] = []
    position: dict[str, int] = {}

    def visit(node: str) -> list[str] | None:
        if state[node] == 2:
            return None
        if state[node] == 1:
            return [*stack[position[node] :], node]
        state[node] = 1
        position[node] = len(stack)
        stack.append(node)
        for target in sorted(graph.get(node, set())):
            result = visit(target)
            if result:
                return result
        stack.pop()
        position.pop(node, None)
        state[node] = 2
        return None

    for node in sorted(graph):
        result = visit(node)
        if result:
            return result
    return None


__all__ = [
    "OrganizationBlueprintValidationError",
    "OrganizationBlueprintValidationService",
    "PARENT_KIND_MATRIX",
    "RELATION_ENDPOINT_KIND_MATRIX",
]
