from pydantic import BaseModel, Field
from typing import Optional, List, Any
import uuid

class TaskStepProposeRequest(BaseModel):
    prompt: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    task_id: Optional[str] = None

class TaskStepProposeResponse(BaseModel):
    reason: str
    command: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    raw: str

class TaskStepExecuteRequest(BaseModel):
    command: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    timeout: Optional[int] = 60
    task_id: Optional[str] = None
    retries: Optional[int] = 0
    retry_delay: Optional[int] = 1

class TaskStepExecuteResponse(BaseModel):
    output: str
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    status: Optional[str] = None

class AgentRegisterRequest(BaseModel):
    name: str
    url: str
    role: str = "worker"
    token: Optional[str] = None
    registration_token: Optional[str] = None

class AgentInfo(BaseModel):
    url: str
    role: str
    token: Optional[str] = None
    last_seen: float
    status: str = "online"

class LLMConfig(BaseModel):
    provider: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None

class TemplateCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    prompt_template: str

class LLMGenerateRequest(BaseModel):
    prompt: str
    config: Optional[LLMConfig] = None

class Team(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    type: str = "Scrum"
    agent_names: List[str] = []
    role_templates: dict = {}
    is_active: bool = False

class TeamTypeCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None

class RoleCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    default_template_id: Optional[str] = None

class TeamMemberAssignment(BaseModel):
    agent_url: str
    role_id: str
    custom_template_id: Optional[str] = None

class TeamCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    team_type_id: Optional[str] = None
    members: Optional[List[TeamMemberAssignment]] = []

class TeamUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    team_type_id: Optional[str] = None
    members: Optional[List[TeamMemberAssignment]] = None
    is_active: Optional[bool] = None

class ScheduledTask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    command: str
    interval_seconds: int
    next_run: float
    last_run: Optional[float] = None
    enabled: bool = True

class TaskDelegationRequest(BaseModel):
    agent_url: str
    agent_token: Optional[str] = None
    subtask_description: str
    priority: str = "Medium"
