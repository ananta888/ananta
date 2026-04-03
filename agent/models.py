import uuid
from typing import List, Optional

from sqlmodel import Field, SQLModel


class TaskStepProposeRequest(SQLModel):
    prompt: Optional[str] = None
    provider: Optional[str] = None
    providers: Optional[List[str]] = None
    model: Optional[str] = None
    task_id: Optional[str] = None
    research_context: Optional["ResearchContextInputContract"] = None


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
    task_kind: Optional[str] = None
    retries: Optional[int] = 0
    retry_delay: Optional[int] = 1
    retry_policy_override: Optional[dict] = None


class TaskStepExecuteResponse(SQLModel):
    output: str
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    status: Optional[str] = None
    retry_history: Optional[List[dict]] = None
    cost_summary: Optional[dict] = None


class CostSummaryContract(SQLModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    task_kind: Optional[str] = None
    tokens_total: int = 0
    cost_units: float = 0.0
    latency_ms: Optional[int] = None
    pricing_source: Optional[str] = None


class TaskCliResultContract(SQLModel):
    returncode: int = 0
    latency_ms: int = 0
    stderr_preview: Optional[str] = None


class TaskRoutingContract(SQLModel):
    task_kind: Optional[str] = None
    effective_backend: Optional[str] = None
    reason: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)
    research_specialization: Optional[str] = None


class TaskReviewStateContract(SQLModel):
    required: bool = False
    status: str = "not_required"
    policy_version: Optional[str] = None
    reason: Optional[str] = None
    risk_level: Optional[str] = None
    uses_terminal: bool = False
    uses_file_access: bool = False
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[float] = None
    comment: Optional[str] = None


class TaskWorkerContextSummaryContract(SQLModel):
    context_bundle_id: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    expected_output_schema: dict = Field(default_factory=dict)
    context_chunk_count: int = 0
    has_context_text: bool = False


class TaskArtifactReferenceContract(SQLModel):
    kind: str
    artifact_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    extracted_document_id: Optional[str] = None
    filename: Optional[str] = None
    media_type: Optional[str] = None
    task_id: Optional[str] = None


class TaskScopedStepProposeResponse(SQLModel):
    status: str = "proposing"
    reason: str
    command: Optional[str] = None
    tool_calls: Optional[List[dict]] = None
    raw: Optional[str] = None
    backend: Optional[str] = None
    model: Optional[str] = None
    routing: Optional[TaskRoutingContract] = None
    cli_result: Optional[TaskCliResultContract] = None
    comparisons: Optional[dict] = None
    research_artifact: Optional["ResearchArtifact"] = None
    research_context: Optional["ResearchContextSummaryContract"] = None
    worker_context: Optional[TaskWorkerContextSummaryContract] = None
    trace: Optional[dict] = None
    pipeline: Optional[dict] = None
    review: Optional[TaskReviewStateContract] = None


class TaskScopedStepExecuteResponse(SQLModel):
    output: str
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    status: Optional[str] = None
    retry_history: Optional[List[dict]] = None
    cost_summary: Optional[CostSummaryContract] = None
    trace: Optional[dict] = None
    pipeline: Optional[dict] = None
    memory_entry_id: Optional[str] = None
    retries_used: int = 0
    failure_type: Optional[str] = None
    execution_policy: Optional["TaskExecutionPolicyContract"] = None
    review: Optional[TaskReviewStateContract] = None
    artifacts: Optional[List[TaskArtifactReferenceContract]] = None


class ArtifactUploadRequest(SQLModel):
    collection_name: Optional[str] = None


class ArtifactRagIndexRequest(SQLModel):
    async_mode: bool = Field(default=False, alias="async")
    profile_name: Optional[str] = None
    profile_overrides: Optional[dict] = None


class KnowledgeCollectionCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None


class KnowledgeCollectionIndexRequest(SQLModel):
    async_mode: bool = Field(default=False, alias="async")
    profile_name: Optional[str] = None
    profile_overrides: Optional[dict] = None


class KnowledgeCollectionSearchRequest(SQLModel):
    query: str
    top_k: int = 5


class TaskExecutionPolicyContract(SQLModel):
    timeout_seconds: int
    retries: int
    retry_delay_seconds: int
    source: str
    retry_backoff_strategy: str = "constant"
    max_retry_delay_seconds: int = 60
    jitter_factor: float = 0.0
    retryable_exit_codes: List[int] = Field(default_factory=lambda: [1, -1])
    retry_on_timeouts: bool = True


class TaskStatusContract(SQLModel):
    canonical_values: List[str] = Field(default_factory=list)
    terminal_values: List[str] = Field(default_factory=list)
    active_values: List[str] = Field(default_factory=list)
    autopilot_dispatch_values: List[str] = Field(default_factory=list)
    aliases: dict = Field(default_factory=dict)


class TaskStateTransitionRule(SQLModel):
    action: str
    from_statuses: List[str] = Field(default_factory=list)
    to_status: str


class TaskStateMachineContract(SQLModel):
    transitions: List[TaskStateTransitionRule] = Field(default_factory=list)
    notes: dict = Field(default_factory=dict)


class HealthCheckSection(SQLModel):
    status: str
    message: Optional[str] = None


class RegistrationStateReadModel(SQLModel):
    enabled: bool = False
    thread_started: bool = False
    running: bool = False
    attempts: int = 0
    max_retries: int = 0
    last_attempt_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
    next_retry_at: Optional[float] = None
    registered_as: Optional[str] = None


class SystemHealthReadModel(SQLModel):
    status: str
    agent: Optional[str] = None
    role: Optional[str] = None
    uptime_seconds: int = 0
    checks: dict = Field(default_factory=dict)


class ResearchSource(SQLModel):
    title: str
    url: str
    kind: Optional[str] = "web"
    confidence: Optional[float] = None


class ResearchArtifact(SQLModel):
    kind: str = "research_report"
    summary: str
    report_markdown: str
    sources: List[ResearchSource] = Field(default_factory=list)
    citations: List[dict] = Field(default_factory=list)
    trace: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    backend_metadata: dict = Field(default_factory=dict)


class ResearchContextInputContract(SQLModel):
    artifact_ids: List[str] = Field(default_factory=list)
    knowledge_collection_ids: List[str] = Field(default_factory=list)
    repo_scope_refs: List[dict] = Field(default_factory=list)
    include_extracted_text: bool = True
    include_knowledge_chunks: bool = True
    max_context_chars: int = 12000
    top_k: int = 5


class ResearchContextSummaryContract(SQLModel):
    artifact_ids: List[str] = Field(default_factory=list)
    knowledge_collection_ids: List[str] = Field(default_factory=list)
    repo_scope_refs: List[dict] = Field(default_factory=list)
    artifacts: List[dict] = Field(default_factory=list)
    knowledge_collections: List[dict] = Field(default_factory=list)
    repo_scopes: List[dict] = Field(default_factory=list)
    prompt_section: Optional[str] = None
    truncated: bool = False
    context_char_count: int = 0


class AgentRegisterRequest(SQLModel):
    name: str
    url: str
    role: str = "worker"
    token: Optional[str] = None
    worker_roles: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    execution_limits: dict = Field(default_factory=dict)
    registration_token: Optional[str] = None


class WorkerExecutionLimitsContract(SQLModel):
    max_parallel_tasks: int = 1
    max_runtime_seconds: int = 900
    max_workspace_mb: int = 1024


class AgentLivenessContract(SQLModel):
    status: str = "online"
    last_seen: Optional[float] = None
    stale_seconds: int = 0
    offline_timeout_seconds: int = 0
    available_for_routing: bool = True


class AgentDirectoryEntryContract(SQLModel):
    name: str
    url: str
    role: str = "worker"
    worker_roles: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    execution_limits: WorkerExecutionLimitsContract = Field(default_factory=WorkerExecutionLimitsContract)
    status: str = "online"
    registration_validated: bool = True
    current_load: int = 0
    available_for_routing: bool = True
    routing_signals: dict = Field(default_factory=dict)
    security_level: str = "medium"
    liveness: AgentLivenessContract = Field(default_factory=AgentLivenessContract)


class WorkerRoutingDecisionContract(SQLModel):
    worker_url: Optional[str] = None
    selected_by_policy: bool = False
    strategy: str = "manual_override"
    reasons: List[str] = Field(default_factory=list)
    matched_capabilities: List[str] = Field(default_factory=list)
    matched_roles: List[str] = Field(default_factory=list)
    task_kind: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)
    research_specialization: Optional[str] = None
    preferred_backend: Optional[str] = None


class WorkerExecutionContextContract(SQLModel):
    version: str = "v1"
    kind: str = "worker_execution_context"
    instructions: Optional[str] = None
    context_bundle_id: Optional[str] = None
    context: dict = Field(default_factory=dict)
    context_policy: dict = Field(default_factory=dict)
    allowed_tools: List[str] = Field(default_factory=list)
    expected_output_schema: dict = Field(default_factory=dict)
    routing: Optional[WorkerRoutingDecisionContract] = None


class HubEventContextContract(SQLModel):
    task_id: Optional[str] = None
    goal_id: Optional[str] = None
    trace_id: Optional[str] = None
    plan_id: Optional[str] = None
    verification_record_id: Optional[str] = None


class HubEventContract(SQLModel):
    version: str = "v1"
    kind: str = "hub_event"
    channel: str = "task_history"
    event_type: str
    timestamp: float
    actor: str = "system"
    context: HubEventContextContract = Field(default_factory=HubEventContextContract)
    details: dict = Field(default_factory=dict)


class HubEventCatalogContract(SQLModel):
    version: str = "v1"
    kind: str = "hub_event_catalog"
    channels: dict = Field(default_factory=dict)
    notes: dict = Field(default_factory=dict)


class WorkerJobContract(SQLModel):
    parent_task_id: Optional[str] = None
    subtask_id: Optional[str] = None
    worker_url: str
    context_bundle_id: Optional[str] = None
    status: str = "created"
    allowed_tools: List[str] = Field(default_factory=list)
    expected_output_schema: dict = Field(default_factory=dict)
    job_metadata: dict = Field(default_factory=dict)


class WorkerResultContract(SQLModel):
    worker_job_id: str
    task_id: Optional[str] = None
    worker_url: str
    status: str = "received"
    output: Optional[str] = None
    result_metadata: dict = Field(default_factory=dict)


class ContextBundleContract(SQLModel):
    retrieval_run_id: Optional[str] = None
    task_id: Optional[str] = None
    bundle_type: str = "worker_execution_context"
    context_text: Optional[str] = None
    chunks: List[dict] = Field(default_factory=list)
    token_estimate: int = 0
    bundle_metadata: dict = Field(default_factory=dict)


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


class TaskDelegationRequest(SQLModel):
    agent_url: Optional[str] = None
    agent_token: Optional[str] = None
    subtask_description: str
    priority: str = "Medium"
    task_kind: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)
    context_query: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    expected_output_schema: dict = Field(default_factory=dict)


class TaskCreateRequest(SQLModel):
    id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = "created"
    template_id: Optional[str] = None
    tags: Optional[List[str]] = None
    priority: Optional[str] = "medium"
    team_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: Optional[int] = None
    depends_on: Optional[List[str]] = None
    goal_id: Optional[str] = None
    goal_trace_id: Optional[str] = None
    task_kind: Optional[str] = None
    required_capabilities: Optional[List[str]] = None
    context_bundle_id: Optional[str] = None
    worker_execution_context: Optional[dict] = None
    current_worker_job_id: Optional[str] = None


class TaskUpdateRequest(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: Optional[int] = None
    tags: Optional[List[str]] = None
    depends_on: Optional[List[str]] = None
    goal_id: Optional[str] = None
    goal_trace_id: Optional[str] = None
    task_kind: Optional[str] = None
    required_capabilities: Optional[List[str]] = None


class TaskAssignmentRequest(SQLModel):
    agent_url: Optional[str] = None
    token: Optional[str] = None
    task_kind: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)


class TaskClaimRequest(SQLModel):
    task_id: str
    agent_url: str
    lease_seconds: Optional[int] = 120
    idempotency_key: Optional[str] = None


class FollowupTaskItem(SQLModel):
    description: str
    priority: Optional[str] = "Medium"
    agent_url: Optional[str] = None
    agent_token: Optional[str] = None


class FollowupTaskCreateRequest(SQLModel):
    items: List[FollowupTaskItem]


class ConfigUpdateRequest(SQLModel):
    llm_config: Optional[dict] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    template_variables_allowlist: Optional[List[str]] = None
    llm_tool_allowlist: Optional[List[str]] = None
    llm_tool_denylist: Optional[List[str]] = None


class ScheduledTaskCreateRequest(SQLModel):
    command: str
    interval_seconds: int


class GoalCreateRequest(SQLModel):
    goal: str
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


class SgptExecuteRequest(SQLModel):
    prompt: Optional[str] = None
    options: Optional[List[str]] = Field(default_factory=list)
    use_hybrid_context: Optional[bool] = False
    backend: Optional[str] = None
    model: Optional[str] = None
    task_kind: Optional[str] = None


class SgptContextRequest(SQLModel):
    query: Optional[str] = None
    include_context_text: Optional[bool] = True


class SgptSourceRequest(SQLModel):
    source_path: Optional[str] = None
    max_chars: Optional[int] = 1600


class TriggerConfigureRequest(SQLModel):
    enabled_sources: Optional[List[str]] = None
    webhook_secrets: Optional[dict] = None
    auto_start_planner: Optional[bool] = None
    ip_whitelists: Optional[dict] = None
    rate_limits: Optional[dict] = None
    dedup_enabled: Optional[bool] = None
    dedup_ttl_seconds: Optional[int] = None
    replay_window_seconds: Optional[int] = None


class TriggerTestRequest(SQLModel):
    source: str = "generic"
    payload: dict = Field(default_factory=dict)


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


class TeamSetupScrumRequest(SQLModel):
    name: Optional[str] = "Neues Scrum Team"


class TeamTypeRoleLinkCreateRequest(SQLModel):
    role_id: str


class TeamTypeRoleLinkPatchRequest(SQLModel):
    template_id: Optional[str] = None


TaskScopedStepProposeResponse.model_rebuild()
TaskScopedStepExecuteResponse.model_rebuild()
