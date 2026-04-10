import time
import uuid
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class UserDB(SQLModel, table=True):
    __tablename__ = "users"
    username: str = Field(primary_key=True)
    password_hash: str
    role: str = "user"
    mfa_secret: Optional[str] = None
    mfa_enabled: bool = False
    mfa_backup_codes: List[str] = Field(default=[], sa_column=Column(JSON))
    failed_login_attempts: int = Field(default=0)
    lockout_until: Optional[float] = Field(default=None)


class LoginAttemptDB(SQLModel, table=True):
    __tablename__ = "login_attempts"
    id: Optional[int] = Field(default=None, primary_key=True)
    ip: str = Field(index=True)
    timestamp: float = Field(default_factory=time.time)


class BannedIPDB(SQLModel, table=True):
    __tablename__ = "banned_ips"
    ip: str = Field(primary_key=True)
    banned_until: float
    reason: Optional[str] = None


class AgentInfoDB(SQLModel, table=True):
    __tablename__ = "agents"
    url: str = Field(primary_key=True)
    name: str
    role: str = "worker"
    token: Optional[str] = None
    worker_roles: List[str] = Field(default=[], sa_column=Column(JSON))
    capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    execution_limits: dict = Field(default={}, sa_column=Column(JSON))
    registration_validated: bool = True
    validation_errors: List[str] = Field(default=[], sa_column=Column(JSON))
    validated_at: Optional[float] = None
    last_seen: float = Field(default_factory=time.time)
    status: str = "online"


class TeamTypeDB(SQLModel, table=True):
    __tablename__ = "team_types"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None


class RoleDB(SQLModel, table=True):
    __tablename__ = "roles"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    default_template_id: Optional[str] = Field(default=None, foreign_key="templates.id")


class TeamTypeRoleLink(SQLModel, table=True):
    __tablename__ = "team_type_role_links"
    team_type_id: str = Field(foreign_key="team_types.id", primary_key=True)
    role_id: str = Field(foreign_key="roles.id", primary_key=True)
    template_id: Optional[str] = Field(default=None, foreign_key="templates.id")


class TeamDB(SQLModel, table=True):
    __tablename__ = "teams"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    team_type_id: Optional[str] = Field(default=None, foreign_key="team_types.id")
    blueprint_id: Optional[str] = Field(default=None, foreign_key="team_blueprints.id")
    is_active: bool = False
    role_templates: dict = Field(default={}, sa_column=Column(JSON))
    blueprint_snapshot: dict = Field(default={}, sa_column=Column(JSON))


class TeamMemberDB(SQLModel, table=True):
    __tablename__ = "team_members"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    team_id: str = Field(foreign_key="teams.id")
    agent_url: str = Field(foreign_key="agents.url")
    role_id: str = Field(foreign_key="roles.id")
    blueprint_role_id: Optional[str] = Field(default=None, foreign_key="blueprint_roles.id")
    custom_template_id: Optional[str] = Field(default=None, foreign_key="templates.id")


class TeamBlueprintDB(SQLModel, table=True):
    __tablename__ = "team_blueprints"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    base_team_type_name: Optional[str] = None
    is_seed: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class BlueprintRoleDB(SQLModel, table=True):
    __tablename__ = "blueprint_roles"
    __table_args__ = (
        sa.UniqueConstraint("blueprint_id", "name", name="uq_blueprint_roles_blueprint_name"),
        sa.UniqueConstraint("blueprint_id", "sort_order", name="uq_blueprint_roles_blueprint_sort_order"),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    blueprint_id: str = Field(foreign_key="team_blueprints.id", index=True)
    name: str
    description: Optional[str] = None
    template_id: Optional[str] = Field(default=None, foreign_key="templates.id")
    sort_order: int = 0
    is_required: bool = True
    config: dict = Field(default={}, sa_column=Column(JSON))


class BlueprintArtifactDB(SQLModel, table=True):
    __tablename__ = "blueprint_artifacts"
    __table_args__ = (
        sa.UniqueConstraint("blueprint_id", "title", name="uq_blueprint_artifacts_blueprint_title"),
        sa.UniqueConstraint("blueprint_id", "sort_order", name="uq_blueprint_artifacts_blueprint_sort_order"),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    blueprint_id: str = Field(foreign_key="team_blueprints.id", index=True)
    kind: str
    title: str
    description: Optional[str] = None
    sort_order: int = 0
    payload: dict = Field(default={}, sa_column=Column(JSON))


class ArtifactDB(SQLModel, table=True):
    __tablename__ = "artifacts"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    latest_version_id: Optional[str] = None
    latest_sha256: Optional[str] = None
    latest_media_type: Optional[str] = None
    latest_filename: Optional[str] = None
    size_bytes: int = 0
    status: str = "stored"
    created_by: Optional[str] = None
    artifact_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ArtifactVersionDB(SQLModel, table=True):
    __tablename__ = "artifact_versions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    artifact_id: str = Field(index=True)
    version_number: int = 1
    storage_path: str
    original_filename: str
    media_type: str
    size_bytes: int = 0
    sha256: str
    version_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class ExtractedDocumentDB(SQLModel, table=True):
    __tablename__ = "extracted_documents"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    artifact_id: str = Field(index=True)
    artifact_version_id: str = Field(index=True)
    extraction_status: str = "pending"
    extraction_mode: str = "raw-only"
    text_content: Optional[str] = None
    document_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RetrievalRunDB(SQLModel, table=True):
    __tablename__ = "retrieval_runs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    query: str
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    strategy: dict = Field(default={}, sa_column=Column(JSON))
    chunk_count: int = 0
    token_estimate: int = 0
    policy_version: str = "v1"
    run_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class ContextBundleDB(SQLModel, table=True):
    __tablename__ = "context_bundles"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    retrieval_run_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    bundle_type: str = "worker_execution_context"
    context_text: Optional[str] = None
    chunks: List[dict] = Field(default=[], sa_column=Column(JSON))
    token_estimate: int = 0
    bundle_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class KnowledgeCollectionDB(SQLModel, table=True):
    __tablename__ = "knowledge_collections"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    created_by: Optional[str] = None
    collection_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class KnowledgeLinkDB(SQLModel, table=True):
    __tablename__ = "knowledge_links"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    collection_id: str = Field(index=True)
    artifact_id: str = Field(index=True)
    extracted_document_id: Optional[str] = Field(default=None, index=True)
    link_type: str = "artifact"
    link_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class KnowledgeIndexDB(SQLModel, table=True):
    __tablename__ = "knowledge_indices"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    artifact_id: Optional[str] = Field(default=None, index=True)
    collection_id: Optional[str] = Field(default=None, index=True)
    latest_run_id: Optional[str] = Field(default=None, index=True)
    source_scope: str = "artifact"
    profile_name: str = "default"
    status: str = "pending"
    output_dir: Optional[str] = None
    manifest_path: Optional[str] = None
    index_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_by: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class KnowledgeIndexRunDB(SQLModel, table=True):
    __tablename__ = "knowledge_index_runs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    knowledge_index_id: str = Field(index=True)
    artifact_id: Optional[str] = Field(default=None, index=True)
    collection_id: Optional[str] = Field(default=None, index=True)
    profile_name: str = "default"
    status: str = "pending"
    source_path: Optional[str] = None
    output_dir: Optional[str] = None
    manifest_path: Optional[str] = None
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None
    run_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class TemplateDB(SQLModel, table=True):
    __tablename__ = "templates"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_templates_name"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    prompt_template: str


class ScheduledTaskDB(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    command: str
    interval_seconds: int
    next_run: float
    last_run: Optional[float] = None
    enabled: bool = True


class GoalDB(SQLModel, table=True):
    __tablename__ = "goals"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    trace_id: str = Field(default_factory=lambda: f"goal-{uuid.uuid4().hex}", index=True)
    goal: str
    summary: Optional[str] = None
    status: str = "received"
    source: str = "ui"
    requested_by: Optional[str] = None
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id")
    context: Optional[str] = None
    constraints: List[str] = Field(default=[], sa_column=Column(JSON))
    acceptance_criteria: List[str] = Field(default=[], sa_column=Column(JSON))
    execution_preferences: dict = Field(default={}, sa_column=Column(JSON))
    visibility: dict = Field(default={}, sa_column=Column(JSON))
    workflow_defaults: dict = Field(default={}, sa_column=Column(JSON))
    workflow_overrides: dict = Field(default={}, sa_column=Column(JSON))
    workflow_effective: dict = Field(default={}, sa_column=Column(JSON))
    workflow_provenance: dict = Field(default={}, sa_column=Column(JSON))
    readiness: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanDB(SQLModel, table=True):
    __tablename__ = "plans"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    goal_id: str = Field(index=True)
    trace_id: str = Field(index=True)
    status: str = "draft"
    planning_mode: str = "auto_planner"
    rationale: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanNodeDB(SQLModel, table=True):
    __tablename__ = "plan_nodes"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    plan_id: str = Field(index=True)
    node_key: str = Field(index=True)
    title: str
    description: Optional[str] = None
    priority: str = "Medium"
    status: str = "draft"
    position: int = 0
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))
    rationale: dict = Field(default={}, sa_column=Column(JSON))
    editable: bool = True
    materialized_task_id: Optional[str] = None
    verification_spec: dict = Field(default={}, sa_column=Column(JSON))
    verification_status: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TaskDB(SQLModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "todo"
    priority: str = "Medium"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id")
    assigned_agent_url: Optional[str] = Field(default=None, foreign_key="agents.url")
    assigned_role_id: Optional[str] = Field(default=None, foreign_key="roles.id")
    history: List[dict] = Field(default=[], sa_column=Column(JSON))
    last_proposal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_output: Optional[str] = None
    last_exit_code: Optional[int] = None
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    manual_override_until: Optional[float] = None
    goal_id: Optional[str] = Field(default=None, index=True)
    goal_trace_id: Optional[str] = Field(default=None, index=True)
    plan_id: Optional[str] = Field(default=None, index=True)
    plan_node_id: Optional[str] = Field(default=None, index=True)
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    context_bundle_id: Optional[str] = Field(default=None, index=True)
    worker_execution_context: dict = Field(default={}, sa_column=Column(JSON))
    current_worker_job_id: Optional[str] = Field(default=None, index=True)
    verification_spec: dict = Field(default={}, sa_column=Column(JSON))
    verification_status: dict = Field(default={}, sa_column=Column(JSON))
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: int = 0
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))


class ArchivedTaskDB(SQLModel, table=True):
    __tablename__ = "archived_tasks"
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "archived"
    priority: str = "Medium"
    created_at: float
    updated_at: float
    archived_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = None
    assigned_agent_url: Optional[str] = None
    assigned_role_id: Optional[str] = None
    history: List[dict] = Field(default=[], sa_column=Column(JSON))
    last_proposal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_output: Optional[str] = None
    last_exit_code: Optional[int] = None
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    manual_override_until: Optional[float] = None
    goal_id: Optional[str] = None
    goal_trace_id: Optional[str] = None
    plan_id: Optional[str] = None
    plan_node_id: Optional[str] = None
    task_kind: Optional[str] = None
    retrieval_intent: Optional[str] = None
    required_context_scope: Optional[str] = None
    preferred_bundle_mode: Optional[str] = None
    required_capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    context_bundle_id: Optional[str] = None
    worker_execution_context: dict = Field(default={}, sa_column=Column(JSON))
    current_worker_job_id: Optional[str] = None
    verification_spec: dict = Field(default={}, sa_column=Column(JSON))
    verification_status: dict = Field(default={}, sa_column=Column(JSON))
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: int = 0
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))


class ConfigDB(SQLModel, table=True):
    __tablename__ = "config"
    key: str = Field(primary_key=True)
    value_json: str  # Wir speichern den Wert als JSON-String


class RefreshTokenDB(SQLModel, table=True):
    __tablename__ = "refresh_tokens"
    token: str = Field(primary_key=True)
    username: str = Field(foreign_key="users.username")
    expires_at: float


class WorkerJobDB(SQLModel, table=True):
    __tablename__ = "worker_jobs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    parent_task_id: Optional[str] = Field(default=None, index=True)
    subtask_id: Optional[str] = Field(default=None, index=True)
    worker_url: str = Field(index=True)
    context_bundle_id: Optional[str] = Field(default=None, index=True)
    status: str = "created"
    allowed_tools: List[str] = Field(default=[], sa_column=Column(JSON))
    expected_output_schema: dict = Field(default={}, sa_column=Column(JSON))
    job_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class WorkerResultDB(SQLModel, table=True):
    __tablename__ = "worker_results"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    worker_job_id: str = Field(index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    worker_url: str
    status: str = "received"
    output: Optional[str] = None
    result_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class MemoryEntryDB(SQLModel, table=True):
    __tablename__ = "memory_entries"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    worker_job_id: Optional[str] = Field(default=None, index=True)
    entry_type: str = "worker_result"
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    artifact_refs: List[dict] = Field(default=[], sa_column=Column(JSON))
    retrieval_tags: List[str] = Field(default=[], sa_column=Column(JSON))
    memory_metadata: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class PasswordHistoryDB(SQLModel, table=True):
    __tablename__ = "password_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(foreign_key="users.username", index=True)
    password_hash: str
    created_at: float = Field(default_factory=time.time)


class StatsSnapshotDB(SQLModel, table=True):
    __tablename__ = "stats_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float = Field(default_factory=time.time, index=True)
    agents: dict = Field(default={}, sa_column=Column(JSON))
    tasks: dict = Field(default={}, sa_column=Column(JSON))
    shell_pool: dict = Field(default={}, sa_column=Column(JSON))
    resources: dict = Field(default={}, sa_column=Column(JSON))


class AuditLogDB(SQLModel, table=True):
    __tablename__ = "audit_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float = Field(default_factory=time.time, index=True)
    username: str
    ip: str
    action: str
    trace_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    plan_id: Optional[str] = Field(default=None, index=True)
    verification_record_id: Optional[str] = Field(default=None, index=True)
    prev_hash: Optional[str] = None
    record_hash: Optional[str] = Field(default=None, index=True)
    details: dict = Field(default={}, sa_column=Column(JSON))


class PolicyDecisionDB(SQLModel, table=True):
    __tablename__ = "policy_decisions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    decision_type: str
    status: str
    worker_url: Optional[str] = None
    policy_name: str
    policy_version: str
    reasons: List[str] = Field(default=[], sa_column=Column(JSON))
    details: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)


class VerificationRecordDB(SQLModel, table=True):
    __tablename__ = "verification_records"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: str = Field(index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    verification_type: str = "quality_gate"
    status: str = "pending"
    spec: dict = Field(default={}, sa_column=Column(JSON))
    results: dict = Field(default={}, sa_column=Column(JSON))
    retry_count: int = 0
    repair_attempts: int = 0
    escalation_reason: Optional[str] = None
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)
