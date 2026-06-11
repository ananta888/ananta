import uuid
from typing import List, Optional

from sqlmodel import Field, SQLModel


class Team(SQLModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    type: str = "Scrum"
    agent_names: List[str] = []
    role_templates: dict = {}
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
    members: Optional[List[TeamMemberAssignment]] = []


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
