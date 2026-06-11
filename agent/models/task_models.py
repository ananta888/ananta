from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class CommitMetadata(SQLModel):
    commit_type: Optional[str] = None
    commit_scope: Optional[str] = None
    commit_subject_hint: Optional[str] = None


class TaskStepProposeRequest(SQLModel):
    prompt: Optional[str] = None
    provider: Optional[str] = None
    providers: Optional[List[str]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    strategy_mode: Optional[str] = None
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
    inference_provider: Optional[str] = None
    inference_model: Optional[str] = None
    execution_backend: Optional[str] = None
    task_kind: Optional[str] = None
    tokens_total: int = 0
    cost_units: float = 0.0
    latency_ms: Optional[int] = None
    pricing_source: Optional[str] = None


class TaskCliResultContract(SQLModel):
    returncode: int = 0
    latency_ms: Optional[int] = 0
    stderr_preview: Optional[str] = None
    output_source: Optional[str] = None
    repair_attempted: bool = False
    repair_backend: Optional[str] = None
    repair_model: Optional[str] = None
    llm_call_profile: Optional[List[dict]] = None


class TaskRoutingContract(SQLModel):
    task_kind: Optional[str] = None
    effective_backend: Optional[str] = None
    requested_backend: Optional[str] = None
    execution_backend: Optional[str] = None
    worker_runtime_path: Optional[str] = None
    worker_profile: Optional[str] = None
    profile_source: Optional[str] = None
    policy_classification_summary: Optional[str] = None
    execution_mode: Optional[str] = None
    inference_provider: Optional[str] = None
    inference_model: Optional[str] = None
    inference_temperature: Optional[float] = None
    inference_base_url: Optional[str] = None
    inference_target_kind: Optional[str] = None
    inference_target_provider_type: Optional[str] = None
    remote_hub: bool = False
    instance_id: Optional[str] = None
    max_hops: Optional[int] = None
    session_mode: Optional[str] = None
    session_id: Optional[str] = None
    session_reused: Optional[bool] = None
    session_turn_id: Optional[str] = None
    live_terminal: Optional[dict] = None
    reason: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)
    research_specialization: Optional[str] = None
    model_profile_id: Optional[str] = None
    model_role: Optional[str] = None
    model_resolver_source: Optional[str] = None
    model_resolver_rank: Optional[int] = None
    model_policy_decisions: List[str] = Field(default_factory=list)
    model_blocked_candidates: List[str] = Field(default_factory=list)
    model_cloud_allowed: Optional[bool] = None
    model_block_secret_context: Optional[bool] = None
    task_class: Optional[str] = None
    intent: Optional[str] = None
    llm_required: Optional[bool] = None
    deterministic_handler_id: Optional[str] = None


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
    instruction_layers: dict = Field(default_factory=dict)


class TaskArtifactReferenceContract(SQLModel):
    kind: str
    artifact_id: Optional[str] = None
    artifact_version_id: Optional[str] = None
    extracted_document_id: Optional[str] = None
    filename: Optional[str] = None
    media_type: Optional[str] = None
    task_id: Optional[str] = None
    worker_job_id: Optional[str] = None
    workspace_relative_path: Optional[str] = None
    content_hash: Optional[str] = None
    provenance_summary: Optional[dict] = None
    trace_bundle_ref: Optional[str] = None


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
    execution_scope: Optional[dict] = None
    execution_provenance: Optional[dict] = None


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


class KnowledgeSourceIndexRequest(SQLModel):
    source_scope: str
    source_id: str
    records: List[dict]
    async_mode: bool = Field(default=False, alias="async")
    profile_name: Optional[str] = None
    source_metadata: Optional[dict] = None


class KnowledgeCollectionSearchRequest(SQLModel):
    query: str
    top_k: int = 5
    source_types: Optional[List[str]] = None


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


class FollowupTaskItem(SQLModel):
    description: str
    priority: Optional[str] = "Medium"
    agent_url: Optional[str] = None
    agent_token: Optional[str] = None


class FollowupTaskCreateRequest(SQLModel):
    items: List[FollowupTaskItem]


class TaskDelegationRequest(SQLModel):
    agent_url: Optional[str] = None
    agent_token: Optional[str] = None
    subtask_description: str
    priority: str = "Medium"
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
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
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: Optional[List[str]] = None
    context_bundle_id: Optional[str] = None
    worker_execution_context: Optional[dict] = None
    current_worker_job_id: Optional[str] = None
    instruction_owner_username: Optional[str] = None
    instruction_profile_id: Optional[str] = None
    instruction_overlay_id: Optional[str] = None
    commit_metadata: Optional[CommitMetadata] = None


class TaskUpdateRequest(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    team_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: Optional[int] = None
    tags: Optional[List[str]] = None
    depends_on: Optional[List[str]] = None
    goal_id: Optional[str] = None
    goal_trace_id: Optional[str] = None
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: Optional[List[str]] = None
    worker_execution_context: Optional[dict] = None
    instruction_owner_username: Optional[str] = None
    instruction_profile_id: Optional[str] = None
    instruction_overlay_id: Optional[str] = None


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


class ScheduledTaskCreateRequest(SQLModel):
    command: str
    interval_seconds: int


class SgptExecuteRequest(SQLModel):
    prompt: Optional[str] = None
    options: Optional[List[str]] = Field(default_factory=list)
    use_hybrid_context: Optional[bool] = False
    backend: Optional[str] = None
    model: Optional[str] = None
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    source_types: Optional[List[str]] = None


class SgptContextRequest(SQLModel):
    query: Optional[str] = None
    include_context_text: Optional[bool] = True
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    source_types: Optional[List[str]] = None


class SgptSourceRequest(SQLModel):
    source_path: Optional[str] = None
    max_chars: Optional[int] = 1600


class SgptSessionCreateRequest(SQLModel):
    backend: Optional[str] = None
    model: Optional[str] = None
    conversation_id: Optional[str] = None
    task_id: Optional[str] = None
    session_metadata: Optional[dict] = None


class SgptSessionTurnRequest(SQLModel):
    prompt: Optional[str] = None
    model: Optional[str] = None
    options: Optional[List[str]] = Field(default_factory=list)
    task_kind: Optional[str] = None


class WorkflowExecutionRequestModel(SQLModel):
    provider: str
    workflow_id: str
    task_id: Optional[str] = None
    goal_id: Optional[str] = None
    trace_id: Optional[str] = None
    input_payload: dict = Field(default_factory=dict)
    dry_run: bool = True
    requested_by: str
    correlation_id: Optional[str] = None

    @field_validator("provider", "workflow_id", "requested_by")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("must not be empty")
        return str(value).strip()


class CandidateFile(SQLModel):
    path: str
    score: float = 0.0
    reason: Optional[str] = None
    source_record_ids: List[str] = Field(default_factory=list)
    source_output_kinds: List[str] = Field(default_factory=list)
    matched_symbols: List[str] = Field(default_factory=list)
    relation_path: Optional[str] = None
    manifest_hash: Optional[str] = None
    sensitivity: str = "internal"
    read_policy: str = "allowed"
    requires_read: bool = False


class ContextFile(SQLModel):
    path: str
    content: str
    sha256: Optional[str] = None
    byte_count: int = 0
    line_count: int = 0
    line_ranges: Optional[List[dict]] = None
    redaction_status: str = "not_redacted"
    read_at: Optional[float] = None
    provenance: Optional[str] = None


class WorkerContextHandoffV3(SQLModel):
    question: str
    context: Optional[str] = None
    depth: Optional[str] = None
    memory_context: Optional[str] = None
    candidate_files: List[CandidateFile] = Field(default_factory=list)
    context_files: List[ContextFile] = Field(default_factory=list)
    manifest_hash: Optional[str] = None
    policy_version: Optional[str] = None
    required_reads: List[str] = Field(default_factory=list)
    worker_context_requests: List[dict] = Field(default_factory=list)


TaskScopedStepProposeResponse.model_rebuild()
TaskScopedStepExecuteResponse.model_rebuild()
