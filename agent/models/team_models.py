import uuid
from typing import Any, List, Literal, Optional

from pydantic import ConfigDict, model_validator
from sqlmodel import Field, SQLModel


class Team(SQLModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    type: str = "Scrum"
    agent_names: List[str] = Field(default_factory=list)
    role_templates: dict = Field(default_factory=dict)
    is_active: bool = False


class TeamTypeCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None


class RoleCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    default_template_id: Optional[str] = None


class TeamMemberAssignment(SQLModel):
    agent_url: str
    role_id: Optional[str] = None
    blueprint_role_id: Optional[str] = None
    custom_template_id: Optional[str] = None


class TeamCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    team_type_id: Optional[str] = None
    members: Optional[List[TeamMemberAssignment]] = Field(default_factory=list)


class TeamUpdateRequest(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    team_type_id: Optional[str] = None
    members: Optional[List[TeamMemberAssignment]] = None
    is_active: Optional[bool] = None


class BlueprintRoleDefinition(SQLModel):
    name: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    sort_order: int = 0
    is_required: bool = True
    config: dict = Field(default_factory=dict)


class BlueprintArtifactDefinition(SQLModel):
    kind: str
    title: str
    description: Optional[str] = None
    sort_order: int = 0
    payload: dict = Field(default_factory=dict)


class BlueprintBundleTemplate(SQLModel):
    name: str
    description: Optional[str] = None
    prompt_template: str


class BlueprintBundleRoleDefinition(SQLModel):
    name: str
    description: Optional[str] = None
    template_name: Optional[str] = None
    sort_order: int = 0
    is_required: bool = True
    config: dict = Field(default_factory=dict)


class BlueprintBundleDefinition(SQLModel):
    name: str
    description: Optional[str] = None
    base_team_type_name: Optional[str] = None
    roles: List[BlueprintBundleRoleDefinition] = Field(default_factory=list)
    artifacts: List[BlueprintArtifactDefinition] = Field(default_factory=list)


class BlueprintBundleMemberAssignment(SQLModel):
    agent_url: str
    role_name: Optional[str] = None
    blueprint_role_name: Optional[str] = None
    custom_template_name: Optional[str] = None


class BlueprintBundleTeamDefinition(SQLModel):
    name: str
    description: Optional[str] = None
    team_type_name: Optional[str] = None
    blueprint_name: Optional[str] = None
    is_active: bool = False
    role_templates: dict = Field(default_factory=dict)
    members: List[BlueprintBundleMemberAssignment] = Field(default_factory=list)


class TeamBlueprintBundle(SQLModel):
    schema_version: str = "1.0"
    mode: str = "full"
    parts: List[str] = Field(default_factory=list)
    blueprint: Optional[BlueprintBundleDefinition] = None
    templates: List[BlueprintBundleTemplate] = Field(default_factory=list)
    team: Optional[BlueprintBundleTeamDefinition] = None
    bundle_metadata: dict = Field(default_factory=dict)


class TeamBlueprintBundleImportRequest(SQLModel):
    conflict_strategy: str = "fail"
    dry_run: bool = False
    bundle: TeamBlueprintBundle


class PortableDefinitionRevision(SQLModel):
    """Portable key/version definition; local database IDs are forbidden."""

    model_config = ConfigDict(extra="forbid")

    key: str
    version: int = Field(ge=1)
    content_hash: str
    lifecycle: Literal["draft", "active", "retired"] = "draft"
    definition: dict[str, Any] = Field(default_factory=dict)


class PortableOrganizationInstance(SQLModel):
    """Portable target-recompile recipe, never a source runtime snapshot."""

    model_config = ConfigDict(extra="forbid")

    instance_key: str = Field(min_length=1, max_length=191)
    definition_ref: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    composition_mode: Literal["standard", "custom"]
    team_count: int | None = Field(default=None, ge=2)
    team_blueprint_counts: dict[str, int] | None = None
    requested_lifecycle: Literal["draft", "validated"] = "draft"
    # Deprecated source-bound fields remain parseable solely so old v2
    # producers receive a precise fail-closed diagnostic during preview.
    organization_id: str | None = None
    definition_revision: str | None = None
    effective_limit_profile_ref: str | None = None
    effective_limit_profile_revision: int | None = Field(
        default=None,
        ge=1,
    )
    effective_limit_profile_hash: str | None = None
    plan_digest: str | None = None
    topology_snapshot: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_composition(self) -> "PortableOrganizationInstance":
        if self.composition_mode == "standard":
            if self.team_count is None or self.team_blueprint_counts is not None:
                raise ValueError("portable_standard_composition_invalid")
        elif self.team_count is not None or not self.team_blueprint_counts:
            raise ValueError("portable_custom_composition_invalid")
        return self


class RedactedOrganizationAssignment(SQLModel):
    """Portable assignment intent requiring an explicit target-local rebind."""

    model_config = ConfigDict(extra="forbid")

    instance_key: str = Field(min_length=1, max_length=191)
    unit_key: str = Field(min_length=1, max_length=191)
    role_slot_key: str = Field(min_length=1, max_length=191)
    principal_ref: str = Field(min_length=1, max_length=191)
    principal_label: str | None = Field(default=None, max_length=255)
    redaction: Literal["pseudonymized"] = "pseudonymized"
    organization_id: str | None = None


class OrganizationBlueprintBundleV2(SQLModel):
    """Closed multi-definition Organization bundle envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"] = "2.0"
    bundle_metadata: dict[str, Any] = Field(default_factory=dict)
    role_templates: List[PortableDefinitionRevision] = Field(default_factory=list)
    team_blueprints: List[PortableDefinitionRevision] = Field(default_factory=list)
    workflow_definitions: List[PortableDefinitionRevision] = Field(default_factory=list)
    organization_blueprints: List[PortableDefinitionRevision] = Field(default_factory=list)
    handoff_definitions: List[PortableDefinitionRevision] = Field(default_factory=list)
    policies: List[PortableDefinitionRevision] = Field(default_factory=list)
    limit_profiles: List[PortableDefinitionRevision] = Field(default_factory=list)
    organization_instances: List[PortableOrganizationInstance] = Field(
        default_factory=list,
        description="Portable target-recompile recipes without source scope or runtime IDs.",
    )
    include_assignments: bool = Field(
        default=False,
        description="Whether pseudonymized assignment intents are included.",
    )
    assignments: List[RedactedOrganizationAssignment] = Field(
        default_factory=list,
        description="Pseudonymized intents; import requires an explicit target-local principal rebind.",
    )

    @model_validator(mode="after")
    def validate_optional_runtime_sections(self) -> "OrganizationBlueprintBundleV2":
        if self.assignments and not self.include_assignments:
            raise ValueError("organization_bundle_assignment_flag_mismatch")
        instance_keys = {value.instance_key for value in self.organization_instances}
        if len(instance_keys) != len(self.organization_instances):
            raise ValueError("organization_bundle_instance_key_duplicate")
        if any(value.instance_key not in instance_keys for value in self.assignments):
            raise ValueError("organization_bundle_assignment_instance_missing")
        return self


class OrganizationBlueprintBundleV2ImportRequest(SQLModel):
    conflict_strategy: Literal["fail", "skip", "overwrite"] = "fail"
    dry_run: bool = True
    bundle: OrganizationBlueprintBundleV2


class TeamBlueprintCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    base_team_type_name: Optional[str] = None
    roles: List[BlueprintRoleDefinition] = Field(default_factory=list)
    artifacts: List[BlueprintArtifactDefinition] = Field(default_factory=list)


class TeamBlueprintUpdateRequest(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_team_type_name: Optional[str] = None
    roles: Optional[List[BlueprintRoleDefinition]] = None
    artifacts: Optional[List[BlueprintArtifactDefinition]] = None


class TeamBlueprintInstantiateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    activate: bool = False
    members: List[TeamMemberAssignment] = Field(default_factory=list)


class TeamSetupScrumRequest(SQLModel):
    name: Optional[str] = "Neues Scrum Team"
    blueprint_name: Optional[str] = None


class TeamTypeRoleLinkCreateRequest(SQLModel):
    role_id: str


class TeamTypeRoleLinkPatchRequest(SQLModel):
    template_id: Optional[str] = None
