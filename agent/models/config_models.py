from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class ConfigUpdateRequest(SQLModel):
    llm_config: Optional[dict] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    template_variables_allowlist: Optional[List[str]] = None
    llm_tool_allowlist: Optional[List[str]] = None
    llm_tool_denylist: Optional[List[str]] = None


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


class LLMConfig(SQLModel):
    provider: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class LLMGenerateRequest(SQLModel):
    prompt: str
    config: Optional[LLMConfig] = None


class TemplateCreateRequest(SQLModel):
    name: str
    description: Optional[str] = None
    prompt_template: str
    validation_context: Optional[str] = None


class TemplateValidationRequest(SQLModel):
    prompt_template: str
    context_scope: Optional[str] = None


class TemplatePreviewRequest(SQLModel):
    prompt_template: str
    context_scope: Optional[str] = None
    sample_context: Optional[str] = None
    context_payload: dict = Field(default_factory=dict)


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


class ProviderDescriptorContract(SQLModel):
    provider: str
    display_name: str
    enabled: bool
    configured: bool
    mode: str
    supports_model: bool
    install_hint: Optional[str] = None


class ApiErrorResponseContract(SQLModel):
    status: str = "error"
    message: str
    data: Optional[dict] = None


class AgentRegisterRequest(SQLModel):
    name: str
    url: str
    role: str = "worker"
    token: Optional[str] = None
    worker_roles: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    runtime_targets: List[dict] = Field(default_factory=list)
    execution_limits: dict = Field(default_factory=dict)
    worker_kind: Optional[str] = None
    strategy_mode: Optional[str] = None
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
    runtime_targets: List[dict] = Field(default_factory=list)
    execution_limits: WorkerExecutionLimitsContract = Field(default_factory=WorkerExecutionLimitsContract)
    status: str = "online"
    registration_validated: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    current_load: int = 0
    reported_load: int = 0
    scheduler_load: int = 0
    available_for_routing: bool = True
    routing_signals: dict = Field(default_factory=dict)
    security_level: str = "medium"
    strategy_mode: Optional[str] = None
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
    worker_profile: Optional[str] = None
    profile_source: Optional[str] = None


class WorkerExecutionContextContract(SQLModel):
    version: str = "v1"
    kind: str = "worker_execution_context"
    instructions: Optional[str] = None
    context_bundle_id: Optional[str] = None
    context: dict = Field(default_factory=dict)
    context_policy: dict = Field(default_factory=dict)
    workspace: dict = Field(default_factory=dict)
    artifact_sync: dict = Field(default_factory=dict)
    allowed_tools: List[str] = Field(default_factory=list)
    expected_output_schema: dict = Field(default_factory=dict)
    worker_profile: Optional[str] = None
    profile_source: Optional[str] = None
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


class ExposurePolicyGroupContract(SQLModel):
    enabled: bool = False
    allow_agent_auth: bool = False
    allow_user_auth: bool = False
    require_admin_for_user_auth: bool = True
    emit_audit_events: bool = True
    instance_id: Optional[str] = None
    max_hops: Optional[int] = 3
    allow_files_api: Optional[bool] = None


class ExposurePolicyContract(SQLModel):
    openai_compat: ExposurePolicyGroupContract
    mcp: ExposurePolicyGroupContract
    remote_hubs: ExposurePolicyGroupContract


class TerminalPolicyContract(SQLModel):
    enabled: bool = False
    allow_read: bool = False
    allow_interactive: bool = False
    require_admin: bool = True
    emit_audit_events: bool = True
    max_session_seconds: int = 1800
    idle_timeout_seconds: int = 300
    input_preview_max_chars: int = 120
    allowed_roles: List[str] = Field(default_factory=list)
    allowed_cidrs: List[str] = Field(default_factory=list)


class GovernancePolicyReadModelContract(SQLModel):
    exposure_policy: ExposurePolicyContract
    terminal_policy: TerminalPolicyContract
    platform_mode: str
    is_custom: bool = False


class OpenAIModelContract(SQLModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "ananta"
    root: Optional[str] = None
    parent: Optional[str] = None
    permission: List[dict] = Field(default_factory=list)


class OpenAIChatCompletionChoice(SQLModel):
    index: int
    message: dict
    finish_reason: str = "stop"


class OpenAIChatCompletionContract(SQLModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChatCompletionChoice]
    usage: dict
    trace_id: Optional[str] = None
    conversation: Optional[dict] = None


class OpenAIResponseOutput(SQLModel):
    type: str = "message"
    role: str = "assistant"
    content: List[dict]


class OpenAIResponseContract(SQLModel):
    id: str
    object: str = "response"
    created_at: int
    model: str
    output: List[OpenAIResponseOutput]
    output_text: str
    usage: dict
    trace_id: Optional[str] = None
    conversation: Optional[dict] = None


class OpenAIFileContract(SQLModel):
    id: str
    object: str = "file"
    bytes: int
    created_at: int
    filename: str
    purpose: str = "assistants"
    status: str
    media_type: Optional[str] = None
    sha256: Optional[str] = None
    version_id: Optional[str] = None
    extracted_document_count: int = 0
