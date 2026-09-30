"""Closed request/response contracts, error and read port of Organization topology patches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session

from agent.models.organization_models import (
    AssignmentPolicyDefinition,
    SeparationOfDutiesDefinition,
    TeamBlueprintDefinition,
    VersionedDefinitionRef,
)

_RELATION_KINDS = Literal[
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


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TopologyAddValue(_Closed):
    stable_key: str = Field(min_length=1, max_length=191)
    name: str = Field(min_length=1, max_length=255)
    team_blueprint_ref: str | None = None
    slot_key: str | None = None
    role_template_ref: str | None = None
    required: bool | None = None
    min_count: int | None = Field(default=None, ge=0)
    default_count: int | None = Field(default=None, ge=0)
    max_count: int | None = Field(default=None, ge=1)
    assignment_policy: AssignmentPolicyDefinition | None = None
    separation_of_duties: SeparationOfDutiesDefinition | None = None
    overlays: list[str] = Field(default_factory=list)


class TopologyAddOperation(_Closed):
    op: Literal["add"]
    node_kind: Literal["coordination_unit", "value_stream", "team", "role_slot"]
    parent_id: str = Field(min_length=1, max_length=191)
    value: TopologyAddValue

    @model_validator(mode="after")
    def validate_kind_payload(self) -> "TopologyAddOperation":
        required_slot_fields = {
            "slot_key",
            "role_template_ref",
            "required",
            "min_count",
            "default_count",
            "max_count",
            "assignment_policy",
            "separation_of_duties",
        }
        if self.node_kind == "role_slot":
            # ``max_count=null`` deliberately means unbounded, so presence and
            # value must be checked separately.
            if not required_slot_fields.issubset(self.value.model_fields_set) or any(
                getattr(self.value, field) is None for field in required_slot_fields - {"max_count"}
            ):
                raise ValueError("organization_patch_role_slot_payload_incomplete")
            if self.value.team_blueprint_ref is not None:
                raise ValueError("organization_patch_role_slot_team_blueprint_forbidden")
            if not (
                int(self.value.min_count or 0) <= int(self.value.default_count or 0) <= int(self.value.max_count)
                if self.value.max_count is not None
                else int(self.value.min_count or 0) <= int(self.value.default_count or 0)
            ):
                raise ValueError("organization_patch_role_slot_cardinality_invalid")
            if self.value.required and int(self.value.min_count or 0) < 1:
                raise ValueError("organization_patch_required_role_slot_minimum_invalid")
        elif required_slot_fields & self.value.model_fields_set:
            raise ValueError("organization_patch_unit_slot_fields_forbidden")
        elif self.node_kind == "team":
            VersionedDefinitionRef.parse(str(self.value.team_blueprint_ref or ""))
        elif self.value.team_blueprint_ref is not None:
            raise ValueError("organization_patch_structural_team_blueprint_forbidden")
        return self


class TopologyMigrationTarget(_Closed):
    organization_id: str = Field(min_length=1, max_length=191)
    unit_id: str = Field(min_length=1, max_length=191)
    team_id: str = Field(min_length=1, max_length=191)
    role_slot_id: str = Field(min_length=1, max_length=191)


class TopologyRemoveOperation(_Closed):
    op: Literal["remove"]
    node_id: str = Field(min_length=1, max_length=191)
    lifecycle_strategy: Literal["drain", "migrate", "archive"]
    migration_target: TopologyMigrationTarget | None = None

    @model_validator(mode="after")
    def validate_migration_target(self) -> "TopologyRemoveOperation":
        if self.lifecycle_strategy == "migrate" and self.migration_target is None:
            raise ValueError("organization_patch_migration_target_required")
        if self.lifecycle_strategy != "migrate" and self.migration_target is not None:
            raise ValueError("organization_patch_migration_target_unexpected")
        return self


class TopologyReparentOperation(_Closed):
    op: Literal["reparent"]
    node_id: str = Field(min_length=1, max_length=191)
    parent_id: str = Field(min_length=1, max_length=191)
    lifecycle_strategy: Literal["drain", "migrate"] | None = None


class TopologyConnectOperation(_Closed):
    op: Literal["connect"]
    namespace: Literal["organization"]
    edge_kind: _RELATION_KINDS
    source_id: str = Field(min_length=1, max_length=191)
    target_id: str = Field(min_length=1, max_length=191)
    relation_key: str | None = Field(default=None, max_length=191)
    dependency_policy: Literal["advisory", "declared", "gate"] = "declared"
    handoff_contract_ref: str | None = None
    escalation_policy: str = Field(default="hub", min_length=1, max_length=191)

    @model_validator(mode="after")
    def validate_connect(self) -> "TopologyConnectOperation":
        if self.source_id == self.target_id:
            raise ValueError("organization_patch_relation_self_reference")
        if self.handoff_contract_ref:
            VersionedDefinitionRef.parse(self.handoff_contract_ref)
        return self


class TopologyAssignOperation(_Closed):
    op: Literal["assign"]
    role_slot_id: str = Field(min_length=1, max_length=191)
    agent_id: str = Field(min_length=1, max_length=512)


TopologyPatchOperation = Annotated[
    TopologyAddOperation
    | TopologyRemoveOperation
    | TopologyReparentOperation
    | TopologyConnectOperation
    | TopologyAssignOperation,
    Field(discriminator="op"),
]


class OrganizationTopologyPatchDocument(_Closed):
    expected_revision: str = Field(min_length=1, max_length=191)
    operations: list[TopologyPatchOperation] = Field(min_length=1)


class OrganizationTopologyPatchPreview(_Closed):
    schema_version: Literal["1.0"] = "1.0"
    tenant_id: str
    project_id: str
    organization_id: str
    principal_id: str
    expected_revision: str
    source_snapshot_hash: str
    patch_digest: str
    expires_at: str
    expires_at_epoch: float
    effective_limit_profile_ref: str
    effective_limit_profile_revision: int
    effective_limit_profile_hash: str
    effective_policy_hash: str
    budget_policy_hash: str
    operations: list[TopologyPatchOperation]
    planned_writes: list[str]
    diagnostics: list[dict[str, Any]]
    limits: dict[str, Any]
    applicable: bool

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"patch_digest"})


class OrganizationTopologyPatchApplyResult(_Closed):
    organization_id: str
    definition_revision: str
    snapshot_hash: str
    patch_digest: str
    applied_operations: int
    replayed: bool = False


class OrganizationTopologyPatchGrantResult(_Closed):
    grant_id: str
    grant_kind: Literal["topology_patch"] = "topology_patch"
    tenant_id: str
    project_id: str
    organization_id: str
    principal_id: str
    patch_digest: str
    policy_hash: str
    limit_hash: str
    expected_revision: str
    expires_at: float
    replayed: bool = False


class OrganizationTopologyPatchError(RuntimeError):
    def __init__(self, reason_code: str, *, public_status: int = 409) -> None:
        self.reason_code = reason_code
        self.public_status = public_status
        super().__init__(reason_code)


@dataclass(slots=True)
class OrganizationPatchState:
    organization: Any
    snapshot: Any
    units: tuple[Any, ...]
    team_links: tuple[Any, ...]
    role_slots: tuple[Any, ...]
    assignments: tuple[Any, ...]
    relations: tuple[Any, ...]
    team_blueprints: dict[str, TeamBlueprintDefinition]
    team_blueprint_rows: dict[str, Any]
    role_template_refs: frozenset[str]
    workflow_steps: dict[str, int]
    agents: dict[str, Any]
    global_assignment_count_by_agent: dict[str, int]
    activity_by_unit: dict[str, dict[str, int]]
    effective_policy_hash: str
    budget_policy_hash: str | None
    handoff_definition_refs: frozenset[str]


class OrganizationPatchReadPort(Protocol):
    def load_state(
        self,
        *,
        tenant_id: str,
        project_id: str,
        organization_id: str,
        agent_ids: set[str],
        session: Session | None = None,
        for_update: bool = False,
    ) -> OrganizationPatchState | None: ...


__all__ = [
    "OrganizationPatchReadPort",
    "OrganizationPatchState",
    "OrganizationTopologyPatchApplyResult",
    "OrganizationTopologyPatchDocument",
    "OrganizationTopologyPatchError",
    "OrganizationTopologyPatchGrantResult",
    "OrganizationTopologyPatchPreview",
    "TopologyAddOperation",
    "TopologyAddValue",
    "TopologyAssignOperation",
    "TopologyConnectOperation",
    "TopologyMigrationTarget",
    "TopologyPatchOperation",
    "TopologyRemoveOperation",
    "TopologyReparentOperation",
]
