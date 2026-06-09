from __future__ import annotations

import time
import uuid
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


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


class BlueprintWorkflowStepDB(SQLModel, table=True):
    """Persistent representation of a blueprint's optional workflow step."""
    __tablename__ = "blueprint_workflow_steps"
    __table_args__ = (
        sa.UniqueConstraint("blueprint_id", "step_id", name="uq_blueprint_workflow_steps_blueprint_step_id"),
        sa.UniqueConstraint("blueprint_id", "sort_order", name="uq_blueprint_workflow_steps_blueprint_sort_order"),
    )
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    blueprint_id: str = Field(foreign_key="team_blueprints.id", index=True)
    step_id: str = Field(index=True)
    role_name: str = Field(index=True)
    task_kind: str = "coding"
    title: Optional[str] = None
    description: Optional[str] = None
    sort_order: int = 0
    produces: List[str] = Field(default=[], sa_column=Column(JSON))
    consumes: List[str] = Field(default=[], sa_column=Column(JSON))
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))
    gate: bool = False
    checks: dict = Field(default={}, sa_column=Column(JSON))
    failure_policy: Optional[str] = None
    required_capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    pattern_hints: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


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


class ContextAccessPolicyDB(SQLModel, table=True):
    __tablename__ = "context_access_policies"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    policy_id: str = Field(index=True)
    version: int = Field(default=1)
    project_id: Optional[str] = Field(default=None, index=True)
    scope: str = "project"
    enabled: bool = True
    policy_json: dict = Field(default={}, sa_column=Column(JSON))
    lint_status: str = "valid"
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class TerminalSessionDB(SQLModel, table=True):
    __tablename__ = "terminal_sessions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None
    idle_expires_at: Optional[float] = None
    created_by_user_id: str = Field(index=True)
    created_by_username: str = Field(index=True)
    auth_source: str = "user_jwt"
    target_type: str = Field(index=True)
    target_id: str = Field(index=True)
    target_display_name: Optional[str] = None
    workspace_path: Optional[str] = None
    goal_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    tmux_session_name: Optional[str] = Field(default=None, index=True)
    status: str = "created"
    read_only: bool = False
    recording_enabled: bool = False
    last_attach_at: Optional[float] = None
    last_input_at: Optional[float] = None
    last_output_at: Optional[float] = None
    policy_decision_id: str = Field(index=True)
    risk_class: str = "terminal_read"
    metadata_json: dict = Field(default={}, sa_column=Column(JSON))


class TerminalEventDB(SQLModel, table=True):
    __tablename__ = "terminal_events"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    timestamp: float = Field(default_factory=time.time, index=True)
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    event_type: str = Field(index=True)
    target_type: str = Field(index=True)
    target_id: str = Field(index=True)
    operation: str
    allowed: bool
    reason_code: str
    summary: Optional[str] = None
    redaction_applied: bool = False
    metadata_json: dict = Field(default={}, sa_column=Column(JSON))
