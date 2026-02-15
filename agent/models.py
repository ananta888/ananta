from sqlmodel import SQLModel, Field
from typing import Optional, List
import uuid


class TaskStepProposeRequest(SQLModel):
    prompt: Optional[str] = None
    provider: Optional[str] = None
    providers: Optional[List[str]] = None
    model: Optional[str] = None
    task_id: Optional[str] = None


class TaskStepProposeResponse(SQLModel):
    reason: str
    command: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    raw: str
    comparisons: Optional[dict] = None


class TaskStepExecuteRequest(SQLModel):
    command: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    timeout: Optional[int] = 60
    task_id: Optional[str] = None
    retries: Optional[int] = 0
    retry_delay: Optional[int] = 1


class TaskStepExecuteResponse(SQLModel):
    output: str
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    status: Optional[str] = None


class AgentRegisterRequest(SQLModel):
    name: str
    url: str
    role: str = "worker"
    token: Optional[str] = None
    registration_token: Optional[str] = None


class LLMConfig(SQLModel):
    provider: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class TemplateCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    prompt_template: str


class LLMGenerateRequest(SQLModel):
    prompt: str
    config: Optional[LLMConfig] = None


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
    role_id: str
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


class TaskDelegationRequest(SQLModel):
    agent_url: str
    agent_token: Optional[str] = None
    subtask_description: str
    priority: str = "Medium"


class TaskCreateRequest(SQLModel):
    id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "created"
    template_id: Optional[str] = None
    tags: Optional[List[str]] = None
    priority: Optional[str] = "medium"


class TaskUpdateRequest(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[List[str]] = None


class TaskAssignmentRequest(SQLModel):
    agent_url: str
    token: Optional[str] = None


class ConfigUpdateRequest(SQLModel):
    llm_config: Optional[dict] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    template_variables_allowlist: Optional[List[str]] = None
    llm_tool_allowlist: Optional[List[str]] = None
    llm_tool_denylist: Optional[List[str]] = None


class SgptExecuteRequest(SQLModel):
    prompt: Optional[str] = None
    options: Optional[List[str]] = Field(default_factory=list)
    use_hybrid_context: Optional[bool] = False
    backend: Optional[str] = None
    model: Optional[str] = None


class SgptContextRequest(SQLModel):
    query: Optional[str] = None
    include_context_text: Optional[bool] = True


class SgptSourceRequest(SQLModel):
    source_path: Optional[str] = None
    max_chars: Optional[int] = 1600
