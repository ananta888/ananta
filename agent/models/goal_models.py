from typing import List, Optional

from sqlmodel import Field, SQLModel


class GoalCreateRequest(SQLModel):
    goal: Optional[str] = None
    context: Optional[str] = None
    source: Optional[str] = "ui"
    team_id: Optional[str] = None
    create_tasks: Optional[bool] = None
    use_template: Optional[bool] = None
    use_repo_context: Optional[bool] = None
    constraints: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    execution_preferences: dict = Field(default_factory=dict)
    visibility: dict = Field(default_factory=dict)
    workflow: dict = Field(default_factory=dict)
    mode: Optional[str] = "generic"
    mode_data: dict = Field(default_factory=dict)
    instruction_owner_username: Optional[str] = None
    instruction_profile_id: Optional[str] = None
    instruction_overlay_id: Optional[str] = None


class GoalPlanNodePatchRequest(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class GoalProvisionRequest(SQLModel):
    goal: str
    summary: Optional[str] = None
    status: Optional[str] = "planned"
    source: Optional[str] = "test"
    team_id: Optional[str] = None
    context: Optional[str] = None
    constraints: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    execution_preferences: dict = Field(default_factory=dict)
    visibility: dict = Field(default_factory=dict)


class AutoPlannerConfigureRequest(SQLModel):
    enabled: Optional[bool] = None
    auto_followup_enabled: Optional[bool] = None
    max_subtasks_per_goal: Optional[int] = None
    default_priority: Optional[str] = None
    auto_start_autopilot: Optional[bool] = None
    llm_timeout: Optional[int] = None
    llm_retry_attempts: Optional[int] = None
    llm_retry_backoff: Optional[float] = None


class AutoPlannerPlanRequest(SQLModel):
    goal: str
    context: Optional[str] = None
    team_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    create_tasks: Optional[bool] = True
    use_template: Optional[bool] = True
    use_repo_context: Optional[bool] = True


class AutoPlannerAnalyzeRequest(SQLModel):
    output: Optional[str] = None
    exit_code: Optional[int] = None
