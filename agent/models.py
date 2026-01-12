from pydantic import BaseModel, Field
from typing import Optional, List, Any

class TaskStepProposeRequest(BaseModel):
    prompt: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    task_id: Optional[str] = None

class TaskStepProposeResponse(BaseModel):
    reason: str
    command: str
    raw: str

class TaskStepExecuteRequest(BaseModel):
    command: Optional[str] = None
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
