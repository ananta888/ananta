"""Pure, deterministic compiler from an organization definition to a write plan."""

from __future__ import annotations

import uuid
from collections import Counter

from agent.models.organization_models import (
    CompiledOrganizationPlan,
    CompiledOrganizationRelation,
    CompiledOrganizationUnit,
    CompiledRoleSlot,
    OrganizationCompileRequest,
    OrganizationDiagnostic,
    VersionedDefinitionRef,
    canonical_definition_sha256,
    canonical_sha256,
)
from agent.ports.organization_definitions import (
    OrganizationAdmissionPolicyPort,
    OrganizationDefinitionCatalogPort,
    OrganizationLimitProfilePort,
)
from agent.services.organization_blueprint_validation_service import (
    OrganizationBlueprintValidationService,
)
from agent.services.organization_custom_composition_service import (
    OrganizationCustomCompositionError,
    OrganizationCustomCompositionService,
    custom_composition_digest,
)


class OrganizationCompilationError(ValueError):
    def __init__(self, reason_code: str, *, path: str = "$", details: dict | None = None) -> None:
        self.reason_code = reason_code
        self.path = path
        self.details = details or {}
        super().__init__(reason_code)


class OrganizationBlueprintCompiler:
    """Compile through read-only ports; this class never receives a repository UoW."""

    def __init__(
        self,
        *,
        definitions: OrganizationDefinitionCatalogPort,
        limit_profiles: OrganizationLimitProfilePort,
        admission_policy: OrganizationAdmissionPolicyPort,
        validator: OrganizationBlueprintValidationService | None = None,
        custom_compositions: OrganizationCustomCompositionService | None = None,
    ) -> None:
        self._definitions = definitions
        self._limit_profiles = limit_profiles
        self._admission_policy = admission_policy
        self._validator = validator or OrganizationBlueprintValidationService()
        self._custom_compositions = custom_compositions or OrganizationCustomCompositionService()

    def compile(self, request: OrganizationCompileRequest) -> CompiledOrganizationPlan:
        definition_ref = VersionedDefinitionRef.parse(request.definition_ref)
        definition = self._definitions.get_organization_blueprint(definition_ref.key, definition_ref.version)
        if definition is None:
            raise OrganizationCompilationError(
                "ORGANIZATION_BLUEPRINT_NOT_FOUND",
                path="$.definition_ref",
                details={"definition_ref": request.definition_ref},
            )
        limits = self._limit_profiles.resolve_limit_profile(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            policy_ref=definition.limit_policy_ref,
        )
        self._validator.ensure_valid(definition, catalog=self._definitions, limits=limits)

        structural_units = [unit for unit in definition.units if unit.materialization_kind == "structural_unit"]
        singleton_units = {
            unit.unit_key: unit for unit in definition.units if unit.materialization_kind == "team_instance"
        }
        groups_by_id = {group.group_id: group for group in definition.unit_groups}

        if request.composition_mode == "standard":
            singleton_keys, group_counts, requested_count = self._standard_composition(
                definition,
                request.team_count,
            )
            capability_gaps: list[str] = []
            warnings: list[OrganizationDiagnostic] = []
        else:
            try:
                custom = self._custom_compositions.validate(
                    definition=definition,
                    composition=dict(request.custom_composition or {}),
                    maximum_team_count=limits.max_team_instances_per_organization,
                )
            except OrganizationCustomCompositionError as exc:
                raise OrganizationCompilationError(
                    exc.reason_code,
                    path=exc.path,
                    details=exc.details,
                ) from exc
            singleton_keys = list(custom.singleton_unit_keys)
            group_counts = dict(custom.group_counts)
            requested_count = custom.team_count
            composition_digest = custom_composition_digest(
                definition_ref=request.definition_ref,
                definition_revision=canonical_definition_sha256(definition),
                policy_hash=limits.content_hash(),
                composition=custom.team_blueprint_counts,
            )
            accepted, reason = self._admission_policy.validate_exception(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                principal_id=request.principal_id,
                exception_ref=str(request.admission_exception_ref),
                definition_ref=request.definition_ref,
                definition_revision=canonical_definition_sha256(definition),
                policy_hash=limits.content_hash(),
                composition_digest=composition_digest,
                composition=custom.team_blueprint_counts,
            )
            if not accepted:
                raise OrganizationCompilationError(
                    reason or "ORGANIZATION_ADMISSION_EXCEPTION_INVALID",
                    path="$.admission_exception_ref",
                )
            capability_gaps = list(custom.capability_gaps)
            warnings = [
                OrganizationDiagnostic(
                    path="$.custom_composition",
                    reason_code=reason_code,
                    human_message="Custom composition omits a standard organization capability.",
                    severity="warning",
                )
                for reason_code in capability_gaps
            ]

        if requested_count < 2:
            raise OrganizationCompilationError("ORGANIZATION_TEAM_COUNT_BELOW_MINIMUM", path="$.team_count")
        if requested_count > limits.max_team_instances_per_organization:
            raise OrganizationCompilationError(
                "ORGANIZATION_TEAM_LIMIT_EXCEEDED",
                path="$.team_count",
                details={"requested": requested_count, "limit": limits.max_team_instances_per_organization},
            )

        expanded_units: list[CompiledOrganizationUnit] = [
            self._compiled_unit(request, unit.unit_key, unit.unit_kind, unit.parent_unit_ref)
            for unit in structural_units
        ]
        for unit_key in singleton_keys:
            unit = singleton_units.get(unit_key)
            if unit is None:
                raise OrganizationCompilationError(
                    "ORGANIZATION_STANDARD_SINGLETON_NOT_FOUND",
                    path="$.standard_composition.baseline_singleton_team_refs",
                    details={"unit_key": unit_key},
                )
            expanded_units.append(
                self._compiled_unit(
                    request,
                    unit.unit_key,
                    unit.unit_kind,
                    unit.parent_unit_ref,
                    team_blueprint_ref=unit.team_blueprint_ref,
                )
            )
        for group_id in sorted(group_counts):
            group = groups_by_id[group_id]
            for ordinal in range(1, group_counts[group_id] + 1):
                unit_key = f"{group.group_id}:{ordinal:03d}"
                expanded_units.append(
                    self._compiled_unit(
                        request,
                        unit_key,
                        "team",
                        group.parent_unit_ref,
                        team_blueprint_ref=group.team_blueprint_ref,
                        group_id=group.group_id,
                        group_ordinal=ordinal,
                    )
                )

        if len(expanded_units) > limits.max_units_per_organization:
            raise OrganizationCompilationError("ORGANIZATION_UNIT_LIMIT_EXCEEDED", path="$.units")

        active_keys = {unit.unit_key for unit in expanded_units}
        role_slots: list[CompiledRoleSlot] = []
        workflows: set[str] = set()
        workflow_ref_counts: Counter[str] = Counter()
        policies: set[str] = {definition.limit_policy_ref, definition.budgets.policy_ref}
        policies.update(group.limit_policy_ref for group in definition.unit_groups)
        for unit in expanded_units:
            if not unit.team_blueprint_ref:
                continue
            blueprint_ref = VersionedDefinitionRef.parse(unit.team_blueprint_ref)
            team_blueprint = self._definitions.get_team_blueprint(blueprint_ref.key, blueprint_ref.version)
            if team_blueprint is None:  # validator normally catches this; retain fail-closed defense.
                raise OrganizationCompilationError("TEAM_BLUEPRINT_NOT_FOUND", path=f"$.units[{unit.unit_key}]")
            if team_blueprint.workflow_ref:
                workflows.add(team_blueprint.workflow_ref)
                workflow_ref_counts[team_blueprint.workflow_ref] += 1
            policies.update(team_blueprint.policies)
            for slot in team_blueprint.role_slots:
                policies.update(slot.overlays)
                role_slots.append(
                    CompiledRoleSlot(
                        planned_id=self._planned_id(request, "slot", f"{unit.unit_key}:{slot.slot_id}"),
                        unit_key=unit.unit_key,
                        slot_key=slot.slot_id,
                        role_template_ref=slot.role_template_ref,
                        required=slot.required,
                        min_count=slot.min_count,
                        default_count=slot.default_count,
                        max_count=slot.max_count,
                        assignment_policy=slot.assignment_policy.model_dump(mode="json"),
                        separation_of_duties=slot.separation_of_duties.model_dump(mode="json"),
                        overlays=slot.overlays,
                    )
                )
        if len(role_slots) > limits.max_role_slots_per_organization:
            raise OrganizationCompilationError("ORGANIZATION_ROLE_SLOT_LIMIT_EXCEEDED", path="$.role_slots")
        assignment_count = sum(slot.default_count for slot in role_slots)
        if assignment_count > limits.max_assignments_per_organization:
            raise OrganizationCompilationError("ORGANIZATION_ASSIGNMENT_LIMIT_EXCEEDED", path="$.role_slots")
        workflow_step_count = 0
        for workflow_ref_value in sorted(workflows):
            workflow_ref = VersionedDefinitionRef.parse(workflow_ref_value)
            workflow = self._definitions.get_workflow_definition(workflow_ref.key, workflow_ref.version)
            if workflow is None:
                raise OrganizationCompilationError("WORKFLOW_DEFINITION_NOT_FOUND", path="$.workflows")
            workflow_step_count += len(workflow.get("steps") or []) * workflow_ref_counts[workflow_ref_value]
        if workflow_step_count > limits.max_workflow_steps_per_organization:
            raise OrganizationCompilationError("ORGANIZATION_WORKFLOW_STEP_LIMIT_EXCEEDED", path="$.workflows")

        relations: list[CompiledOrganizationRelation] = []
        for relation in definition.relations:
            if relation.source_unit_ref not in active_keys or relation.target_unit_ref not in active_keys:
                warnings.append(
                    OrganizationDiagnostic(
                        path=f"$.relations[{relation.relation_id}]",
                        reason_code="ORGANIZATION_OPTIONAL_RELATION_INACTIVE",
                        human_message="Relation was not materialized because an optional endpoint is inactive.",
                        severity="warning",
                        details={
                            "source_active": relation.source_unit_ref in active_keys,
                            "target_active": relation.target_unit_ref in active_keys,
                        },
                    )
                )
                continue
            relations.append(
                CompiledOrganizationRelation(
                    planned_id=self._planned_id(request, "relation", relation.relation_id),
                    relation_key=relation.relation_id,
                    namespace=relation.namespace,
                    kind=relation.kind,
                    source_unit_key=relation.source_unit_ref,
                    target_unit_key=relation.target_unit_ref,
                    handoff_contract_ref=relation.handoff_contract_ref,
                    dependency_policy=relation.dependency_policy,
                    escalation_policy=relation.escalation_policy,
                )
            )
        if len(relations) > limits.max_relations_per_organization:
            raise OrganizationCompilationError("ORGANIZATION_RELATION_LIMIT_EXCEEDED", path="$.relations")

        team_counts = Counter(
            VersionedDefinitionRef.parse(unit.team_blueprint_ref).key
            for unit in expanded_units
            if unit.team_blueprint_ref
        )
        definition_revision = canonical_definition_sha256(definition)
        payload = {
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "organization_id": request.organization_id,
            "definition_ref": request.definition_ref,
            "definition_revision": definition_revision,
            "composition_mode": request.composition_mode,
            "requested_team_count": requested_count,
            "effective_limit_profile_ref": definition.limit_policy_ref,
            "effective_limit_profile_revision": limits.revision,
            "effective_limit_profile_hash": limits.content_hash(),
            "units": [unit.model_dump(mode="json") for unit in expanded_units],
            "role_slots": [slot.model_dump(mode="json") for slot in role_slots],
            "relations": [relation.model_dump(mode="json") for relation in relations],
            "workflows": sorted(workflows),
            "policies": sorted(policies),
            "capability_gaps": capability_gaps,
            "warnings": [item.model_dump(mode="json") for item in warnings],
            "blockers": [],
            "expected_counts": {
                "team": requested_count,
                "unit": len(expanded_units),
                "role_slot": len(role_slots),
                "assignment_capacity_default": assignment_count,
                "workflow_step": workflow_step_count,
                "organization_relation": len(relations),
                "contains": len(expanded_units),
                **{f"team_blueprint:{key}": value for key, value in sorted(team_counts.items())},
            },
        }
        return CompiledOrganizationPlan(**payload, plan_digest=canonical_sha256(payload))

    @staticmethod
    def _standard_composition(definition, requested_count: int | None) -> tuple[list[str], dict[str, int], int]:
        standard = definition.standard_composition
        if requested_count is None or requested_count < standard.minimum or requested_count > standard.maximum:
            raise OrganizationCompilationError(
                "ORGANIZATION_STANDARD_TEAM_COUNT_INVALID",
                path="$.team_count",
                details={"minimum": standard.minimum, "maximum": standard.maximum},
            )
        singleton_keys = list(dict.fromkeys(standard.baseline_singleton_team_refs))
        group_counts = {key: int(value) for key, value in standard.baseline_group_counts.items()}
        active_count = len(singleton_keys) + sum(group_counts.values())
        for unit_key in standard.activation_order:
            if active_count >= requested_count:
                break
            if unit_key not in singleton_keys:
                singleton_keys.append(unit_key)
                active_count += 1
        if active_count < requested_count:
            group_counts[standard.scale_out_group] = (
                group_counts.get(standard.scale_out_group, 0) + requested_count - active_count
            )
            active_count = requested_count
        if active_count != requested_count:
            raise OrganizationCompilationError("ORGANIZATION_STANDARD_COMPOSITION_COUNT_MISMATCH", path="$.team_count")
        return singleton_keys, group_counts, active_count

    @staticmethod
    def _planned_id(request: OrganizationCompileRequest, kind: str, key: str) -> str:
        identity = f"{request.tenant_id}/{request.project_id}/{request.organization_id}/{kind}/{key}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:organization:{identity}"))

    @classmethod
    def _compiled_unit(
        cls,
        request,
        unit_key,
        unit_kind,
        parent_unit_key,
        *,
        team_blueprint_ref=None,
        group_id=None,
        group_ordinal=None,
    ) -> CompiledOrganizationUnit:
        return CompiledOrganizationUnit(
            planned_id=cls._planned_id(request, "unit", unit_key),
            unit_key=unit_key,
            unit_kind=unit_kind,
            parent_unit_key=parent_unit_key,
            team_blueprint_ref=team_blueprint_ref,
            group_id=group_id,
            group_ordinal=group_ordinal,
        )


__all__ = ["OrganizationBlueprintCompiler", "OrganizationCompilationError"]
