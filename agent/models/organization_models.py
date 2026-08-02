"""Database-independent contracts for Hub-owned organization composition.

The models in this module deliberately contain no Flask or persistence
dependencies.  They are shared by validators, compilers and in-memory tests;
SQLModel rows live in :mod:`agent.db_models.organizations`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


def canonical_json(value: Any) -> str:
    """Return the stable JSON representation used by all organization digests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


_DEFINITION_LIST_KEYS = {
    "units": ("unit_key",),
    "unit_groups": ("group_id",),
    "relations": ("relation_id",),
    "role_slots": ("slot_id",),
    "artifacts": ("kind", "title"),
    "steps": ("sort_order", "id", "step_id"),
}
_DEFINITION_SET_LIST_KEYS = {
    "appendix_refs",
    "baseline_singleton_team_refs",
    "forbidden_capabilities",
    "independent_from_external_duties",
    "independent_from_slot_ids",
    "overlays",
    "policies",
    "required_artifact_kinds",
    "required_capabilities",
}


def canonical_definition_payload(value: Any, *, parent_key: str | None = None) -> Any:
    """Normalize order-insensitive definition collections before hashing.

    Explicit order-bearing fields such as ``activation_order`` and dependency
    sequences are intentionally preserved.
    """

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: canonical_definition_payload(item, parent_key=key) for key, item in sorted(value.items())}
    if isinstance(value, list):
        normalized = [canonical_definition_payload(item) for item in value]
        if parent_key in _DEFINITION_SET_LIST_KEYS:
            return sorted(normalized, key=canonical_json)
        order_fields = _DEFINITION_LIST_KEYS.get(parent_key or "")
        if order_fields and all(isinstance(item, dict) for item in normalized):

            def sort_key(item: dict) -> tuple:
                return tuple(canonical_json(item.get(field)) for field in order_fields)

            return sorted(normalized, key=sort_key)
        return normalized
    return value


def canonical_definition_sha256(value: Any) -> str:
    return canonical_sha256(canonical_definition_payload(value))


class ClosedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionedDefinitionRef(ClosedContract):
    key: str = Field(min_length=1, max_length=191)
    version: StrictInt = Field(ge=1)

    @classmethod
    def parse(cls, value: str) -> "VersionedDefinitionRef":
        key, separator, version = str(value or "").rpartition("@")
        if not separator or not key or not version.isdigit():
            raise ValueError("definition_ref_invalid")
        return cls(key=key, version=int(version))

    def portable_ref(self) -> str:
        return f"{self.key}@{self.version}"


class OrganizationLimitProfile(ClosedContract):
    policy_id: str = Field(min_length=1, max_length=191)
    revision: StrictInt = Field(ge=1)
    max_team_instances_per_organization: StrictInt = Field(ge=10)
    max_units_per_organization: StrictInt = Field(ge=1)
    max_role_slots_per_organization: StrictInt = Field(ge=1)
    max_assignments_per_organization: StrictInt = Field(ge=1)
    max_relations_per_organization: StrictInt = Field(ge=1)
    max_workflow_steps_per_organization: StrictInt = Field(ge=1)
    max_bundle_bytes: StrictInt = Field(ge=1)
    max_patch_operations: StrictInt = Field(ge=1)
    topology_default_page_size: StrictInt = Field(ge=1)
    topology_max_page_size: StrictInt = Field(ge=1)
    topology_max_depth: StrictInt = Field(ge=1)
    runtime_overlay_max_events: StrictInt = Field(ge=1)
    canvas_render_node_limit: StrictInt = Field(ge=1)
    canvas_render_edge_limit: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_page_sizes(self) -> "OrganizationLimitProfile":
        if self.topology_default_page_size > self.topology_max_page_size:
            raise ValueError("topology_default_page_size_exceeds_maximum")
        return self

    def content_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class AssignmentPolicyDefinition(ClosedContract):
    principal_kinds: list[Literal["agent", "human"]] = Field(min_length=1)
    required_capabilities: list[str]
    forbidden_capabilities: list[str]
    write_access_required: bool

    @model_validator(mode="after")
    def validate_capabilities(self) -> "AssignmentPolicyDefinition":
        if len(set(self.principal_kinds)) != len(self.principal_kinds):
            raise ValueError("assignment_policy_principal_kinds_duplicate")
        if set(self.required_capabilities) & set(self.forbidden_capabilities):
            raise ValueError("assignment_policy_capability_conflict")
        return self


class SeparationOfDutiesDefinition(ClosedContract):
    enforcement: Literal["none", "warn", "strict"]
    independent_from_slot_ids: list[str]
    independent_from_external_duties: list[str] = Field(default_factory=list)


class TeamArtifactContract(ClosedContract):
    kind: str = Field(min_length=1, max_length=191)
    required: bool
    portable: bool


class TeamCapacityDefaults(ClosedContract):
    min_agents: StrictInt = Field(ge=1)
    default_agents: StrictInt = Field(ge=1)
    max_agents: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def validate_cardinality(self) -> "TeamCapacityDefaults":
        if not self.min_agents <= self.default_agents <= self.max_agents:
            raise ValueError("team_capacity_defaults_invalid")
        return self


class RoleSlotDefinition(ClosedContract):
    slot_id: str = Field(min_length=1, max_length=191)
    role_template_ref: str = Field(min_length=3, max_length=255)
    required: bool
    min_count: StrictInt = Field(ge=0)
    default_count: StrictInt = Field(ge=0)
    max_count: StrictInt | None = Field(ge=1)
    assignment_policy: AssignmentPolicyDefinition
    separation_of_duties: SeparationOfDutiesDefinition
    overlays: list[str]

    @model_validator(mode="after")
    def validate_cardinality(self) -> "RoleSlotDefinition":
        effective_max = self.max_count if self.max_count is not None else self.default_count
        if self.min_count > self.default_count or self.default_count > effective_max:
            raise ValueError("role_slot_cardinality_invalid")
        if self.required and self.min_count < 1:
            raise ValueError("required_role_slot_must_have_positive_minimum")
        VersionedDefinitionRef.parse(self.role_template_ref)
        for overlay in self.overlays:
            VersionedDefinitionRef.parse(overlay)
        return self


class TeamBlueprintDefinition(ClosedContract):
    key: str = Field(min_length=1, max_length=191)
    version: StrictInt = Field(ge=1)
    description: str = Field(min_length=1)
    team_kind: Literal["delivery", "research", "poc", "shared_service", "governance", "coordination"]
    role_slots: list[RoleSlotDefinition] = Field(min_length=1)
    artifacts: list[TeamArtifactContract] = Field(min_length=1)
    workflow_ref: str = Field(min_length=3, max_length=255)
    policies: list[str]
    capacity_defaults: TeamCapacityDefaults

    @model_validator(mode="after")
    def validate_references(self) -> "TeamBlueprintDefinition":
        VersionedDefinitionRef.parse(self.workflow_ref)
        for policy in self.policies:
            VersionedDefinitionRef.parse(policy)
        return self


class TeamCountRange(ClosedContract):
    minimum: Literal[5]
    default: StrictInt = Field(ge=5, le=10)
    maximum: Literal[10]

    @model_validator(mode="after")
    def validate_band(self) -> "TeamCountRange":
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("standard_team_count_band_invalid")
        return self


class StandardCompositionDefinition(ClosedContract):
    team_count_range: TeamCountRange
    baseline_singleton_team_refs: list[str]
    baseline_group_counts: dict[str, StrictInt] = Field(min_length=1)
    activation_order: list[str]
    scale_out_group: str = Field(min_length=1, max_length=191)

    @property
    def minimum(self) -> int:
        return self.team_count_range.minimum

    @property
    def default(self) -> int:
        return self.team_count_range.default

    @property
    def maximum(self) -> int:
        return self.team_count_range.maximum


class UnitGroupCapacityRule(ClosedContract):
    formula: Literal["effective_max_team_instances_minus_active_singleton_teams"]
    minimum_remaining: StrictInt = Field(ge=1)


class OrganizationUnitGroupDefinition(ClosedContract):
    group_id: str = Field(min_length=1, max_length=191)
    team_blueprint_ref: str = Field(min_length=3, max_length=255)
    parent_unit_ref: str = Field(min_length=1, max_length=191)
    min_count: StrictInt = Field(ge=1)
    default_count: StrictInt = Field(ge=1)
    max_count: StrictInt | None = Field(ge=1)
    capacity_rule: UnitGroupCapacityRule
    naming_policy: Literal["stable_group_ordinal"]
    limit_policy_ref: str = Field(min_length=3, max_length=255)
    overrides: dict[str, Any]

    @model_validator(mode="after")
    def validate_cardinality(self) -> "OrganizationUnitGroupDefinition":
        if self.min_count > self.default_count:
            raise ValueError("unit_group_cardinality_invalid")
        if self.max_count is not None and self.default_count > self.max_count:
            raise ValueError("unit_group_cardinality_invalid")
        VersionedDefinitionRef.parse(self.team_blueprint_ref)
        VersionedDefinitionRef.parse(self.limit_policy_ref)
        return self


class OrganizationUnitDefinition(ClosedContract):
    unit_key: str = Field(min_length=1, max_length=191)
    unit_kind: Literal["coordination_unit", "value_stream", "team"]
    materialization_kind: Literal["structural_unit", "team_instance"]
    parent_unit_ref: str | None = None
    team_blueprint_ref: str | None = None
    activation_policy: Literal["always", "baseline", "ordered_optional"]

    @model_validator(mode="after")
    def validate_materialization(self) -> "OrganizationUnitDefinition":
        if self.materialization_kind == "team_instance":
            if self.unit_kind != "team" or not self.team_blueprint_ref:
                raise ValueError("team_unit_blueprint_ref_required")
            if self.parent_unit_ref is None or self.activation_policy == "always":
                raise ValueError("team_unit_parent_or_activation_invalid")
            VersionedDefinitionRef.parse(self.team_blueprint_ref)
        elif self.team_blueprint_ref is not None:
            raise ValueError("structural_unit_team_blueprint_ref_forbidden")
        elif self.unit_kind == "team" or self.activation_policy != "always":
            raise ValueError("structural_unit_kind_or_activation_invalid")
        return self


class OrganizationRelationDefinition(ClosedContract):
    relation_id: str = Field(min_length=1, max_length=191)
    namespace: Literal["organization"]
    source_unit_ref: str = Field(min_length=1, max_length=191)
    target_unit_ref: str = Field(min_length=1, max_length=191)
    kind: Literal[
        "governs",
        "enables",
        "supplies_research_to",
        "prototypes_for",
        "reviews",
        "releases_for",
        "declared_dependency",
        "handoff",
        "escalates_to",
    ]
    activation_condition: Literal["both_endpoints_materialized"]
    handoff_contract_ref: str | None
    dependency_policy: Literal["advisory", "declared", "gate"]
    escalation_policy: str = Field(min_length=1, max_length=191)

    @model_validator(mode="after")
    def validate_handoff_reference(self) -> "OrganizationRelationDefinition":
        if self.handoff_contract_ref is not None:
            VersionedDefinitionRef.parse(self.handoff_contract_ref)
        return self


class SharedProductModelDefinition(ClosedContract):
    goal_scope: Literal["organization"] = "organization"
    team_backlogs: Literal["derived"] = "derived"


class OrganizationOrchestrationDefinition(ClosedContract):
    owner: Literal["hub"] = "hub"
    workers_may_orchestrate: Literal[False] = False


class OrganizationGovernanceDefinition(ClosedContract):
    admission_policy: Literal["standard_or_explicit_custom"] = "standard_or_explicit_custom"
    separation_of_duties: Literal["warn", "strict"] = "strict"


class OrganizationBudgetDefinition(ClosedContract):
    policy_ref: str

    @model_validator(mode="after")
    def validate_policy_ref(self) -> "OrganizationBudgetDefinition":
        VersionedDefinitionRef.parse(self.policy_ref)
        return self


class OrganizationBlueprintDefinition(ClosedContract):
    key: str = Field(min_length=1, max_length=191)
    version: StrictInt = Field(ge=1)
    description: str = Field(min_length=1)
    parameter_schema: dict[str, Any]
    standard_composition: StandardCompositionDefinition
    unit_groups: list[OrganizationUnitGroupDefinition] = Field(min_length=1)
    units: list[OrganizationUnitDefinition] = Field(min_length=1)
    relations: list[OrganizationRelationDefinition]
    shared_product_model: SharedProductModelDefinition
    orchestration: OrganizationOrchestrationDefinition
    governance: OrganizationGovernanceDefinition
    budgets: OrganizationBudgetDefinition
    limit_policy_ref: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def validate_limit_ref(self) -> "OrganizationBlueprintDefinition":
        VersionedDefinitionRef.parse(self.limit_policy_ref)
        return self


class OrganizationCompileRequest(ClosedContract):
    tenant_id: str = Field(min_length=1, max_length=191)
    project_id: str = Field(min_length=1, max_length=191)
    principal_id: str = Field(default="hub", min_length=1, max_length=191)
    organization_id: str = Field(min_length=1, max_length=191)
    definition_ref: str = Field(min_length=3, max_length=255)
    composition_mode: Literal["standard", "custom"]
    team_count: StrictInt | None = None
    custom_composition: dict[str, StrictInt] | None = None
    admission_exception_ref: str | None = None

    @model_validator(mode="after")
    def validate_composition_shape(self) -> "OrganizationCompileRequest":
        VersionedDefinitionRef.parse(self.definition_ref)
        if self.composition_mode == "standard":
            if self.team_count is None or self.custom_composition is not None:
                raise ValueError("standard_composition_shape_invalid")
        elif self.team_count is not None or not self.custom_composition or not self.admission_exception_ref:
            raise ValueError("custom_composition_shape_invalid")
        return self


class OrganizationDiagnostic(ClosedContract):
    path: str
    reason_code: str
    human_message: str
    severity: Literal["warning", "blocker"]
    details: dict[str, Any] = Field(default_factory=dict)


class CompiledOrganizationUnit(ClosedContract):
    planned_id: str
    unit_key: str
    unit_kind: Literal["coordination_unit", "value_stream", "team"]
    parent_unit_key: str | None = None
    team_blueprint_ref: str | None = None
    group_id: str | None = None
    group_ordinal: StrictInt | None = None


class CompiledRoleSlot(ClosedContract):
    planned_id: str
    unit_key: str
    slot_key: str
    role_template_ref: str
    required: bool
    min_count: StrictInt
    default_count: StrictInt
    max_count: StrictInt | None = None
    assignment_policy: dict[str, Any] = Field(default_factory=dict)
    separation_of_duties: dict[str, Any] = Field(default_factory=dict)
    overlays: list[str] = Field(default_factory=list)


class CompiledOrganizationRelation(ClosedContract):
    planned_id: str
    relation_key: str
    namespace: Literal["organization"]
    kind: str
    source_unit_key: str
    target_unit_key: str
    handoff_contract_ref: str | None = None
    dependency_policy: str
    escalation_policy: str | None = None


class CompiledOrganizationPlan(ClosedContract):
    tenant_id: str
    project_id: str
    organization_id: str
    definition_ref: str
    definition_revision: str
    composition_mode: Literal["standard", "custom"]
    requested_team_count: StrictInt
    effective_limit_profile_ref: str
    effective_limit_profile_revision: StrictInt
    effective_limit_profile_hash: str
    units: list[CompiledOrganizationUnit]
    role_slots: list[CompiledRoleSlot]
    relations: list[CompiledOrganizationRelation]
    workflows: list[str]
    policies: list[str]
    capability_gaps: list[str]
    warnings: list[OrganizationDiagnostic]
    blockers: list[OrganizationDiagnostic]
    expected_counts: dict[str, StrictInt]
    plan_digest: str

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"plan_digest"})


class OrganizationTopologyChange(ClosedContract):
    action: Literal["create", "retain", "reparent", "drain", "archive"]
    unit_key: str
    current_parent_unit_key: str | None = None
    target_parent_unit_key: str | None = None
    activity: dict[str, StrictInt] = Field(default_factory=dict)
    requires_confirmation: bool = False


class OrganizationTopologyChangePlan(ClosedContract):
    organization_id: str
    source_snapshot_hash: str
    target_plan_digest: str
    target_team_count: StrictInt
    changes: list[OrganizationTopologyChange]
    warnings: list[OrganizationDiagnostic]
    blockers: list[OrganizationDiagnostic]
    change_plan_digest: str


class OrganizationInstantiationResult(ClosedContract):
    organization_id: str
    definition_revision: str
    plan_digest: str
    topology_snapshot_hash: str
    team_ids: list[str]
    unit_ids: list[str]
    role_slot_ids: list[str]
    relation_ids: list[str]
    organization_admin_grant_id: str
    idempotent_replay: bool = False


class OrganizationBundlePlanItem(ClosedContract):
    section: str
    key: str
    version: StrictInt
    content_hash: str
    action: Literal["create", "update", "unchanged", "skip", "conflict"]
    changes: list[str] = Field(default_factory=list)


class OrganizationBundleImportPlan(ClosedContract):
    schema_version: Literal["2.0"]
    tenant_id: str
    project_id: str
    principal_id: str
    conflict_strategy: Literal["fail", "skip", "overwrite"]
    bundle_digest: str
    expected_target_revision: str
    effective_limit_profile_ref: str
    effective_limit_profile_revision: StrictInt
    effective_limit_profile_hash: str
    expires_at: str
    expires_at_epoch: float
    allowed_source_refs: list[str]
    allowed_run_refs: list[str]
    items: list[OrganizationBundlePlanItem]
    instance_plans: list[CompiledOrganizationPlan] = Field(default_factory=list)
    instance_organization_ids: dict[str, str] = Field(default_factory=dict)
    instance_names: dict[str, str] = Field(default_factory=dict)
    instance_requested_lifecycles: dict[str, Literal["draft", "validated"]] = Field(
        default_factory=dict,
    )
    instance_admission_exception_refs: dict[str, str] = Field(default_factory=dict)
    assignment_rebindings: dict[str, str] = Field(default_factory=dict)
    errors: list[OrganizationDiagnostic]
    plan_digest: str


class OrganizationReconciliationChange(ClosedContract):
    entity_kind: Literal["unit", "role_slot", "relation", "policy", "assignment"]
    entity_key: str
    action: Literal["create", "update", "retain", "archive", "conflict"]
    impact: dict[str, Any] = Field(default_factory=dict)
    preserves_local_override: bool = True


class OrganizationReconciliationPlan(ClosedContract):
    organization_id: str
    source_snapshot_hash: str
    target_plan_digest: str
    changes: list[OrganizationReconciliationChange]
    conflicts: list[OrganizationDiagnostic]
    requires_confirmation: bool
    reconciliation_digest: str


__all__ = [
    "AssignmentPolicyDefinition",
    "CompiledOrganizationPlan",
    "CompiledOrganizationRelation",
    "CompiledOrganizationUnit",
    "CompiledRoleSlot",
    "OrganizationBlueprintDefinition",
    "OrganizationBudgetDefinition",
    "OrganizationBundleImportPlan",
    "OrganizationBundlePlanItem",
    "OrganizationCompileRequest",
    "OrganizationDiagnostic",
    "OrganizationLimitProfile",
    "OrganizationInstantiationResult",
    "OrganizationGovernanceDefinition",
    "OrganizationOrchestrationDefinition",
    "OrganizationRelationDefinition",
    "OrganizationReconciliationChange",
    "OrganizationReconciliationPlan",
    "OrganizationTopologyChange",
    "OrganizationTopologyChangePlan",
    "OrganizationUnitDefinition",
    "OrganizationUnitGroupDefinition",
    "RoleSlotDefinition",
    "SeparationOfDutiesDefinition",
    "SharedProductModelDefinition",
    "StandardCompositionDefinition",
    "TeamArtifactContract",
    "TeamCountRange",
    "TeamBlueprintDefinition",
    "TeamCapacityDefaults",
    "UnitGroupCapacityRule",
    "VersionedDefinitionRef",
    "canonical_json",
    "canonical_definition_payload",
    "canonical_definition_sha256",
    "canonical_sha256",
]
