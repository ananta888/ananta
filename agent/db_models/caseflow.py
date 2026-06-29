"""SQLModel DB tables for CaseFlow Platform and Job Module (CASECORE / DISCOVERY)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CaseFlowCaseDB(SQLModel, table=True):
    __tablename__ = "caseflow_cases"

    id: str = Field(primary_key=True)
    case_type: str
    title: str
    status: str = Field(default="new")
    priority: str = Field(default="medium")
    risk: str = Field(default="low")
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    source: Optional[str] = None
    domain_payload_json: str = Field(default="{}")
    metadata_json: str = Field(default="{}")
    is_deleted: bool = Field(default=False)


class CaseEventDB(SQLModel, table=True):
    __tablename__ = "caseflow_events"

    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    event_type: str
    actor_type: str = Field(default="system")
    actor_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    title: str
    payload_json: str = Field(default="{}")
    trace_id: Optional[str] = None
    artifact_id: Optional[str] = None


class CaseArtifactDB(SQLModel, table=True):
    __tablename__ = "caseflow_artifacts"

    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    artifact_type: str
    artifact_kind: str = Field(default="text")
    title: str
    source: str = Field(default="manual")
    content_ref: Optional[str] = None
    content_text: Optional[str] = None
    mime_type: str = Field(default="text/plain")
    version: int = Field(default=1)
    version_group_id: Optional[str] = None
    previous_artifact_id: Optional[str] = None
    status: str = Field(default="draft")
    created_by: Optional[str] = None
    trace_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    is_sensitive: bool = Field(default=False)
    metadata_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CaseActionDB(SQLModel, table=True):
    __tablename__ = "caseflow_actions"

    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    action_type: str
    title: str
    description: Optional[str] = None
    status: str = Field(default="open")
    due_at: Optional[datetime] = None
    priority: str = Field(default="medium")
    assigned_to: Optional[str] = None
    created_by: str = Field(default="system")
    completed_at: Optional[datetime] = None
    blocking: bool = Field(default=False)
    metadata_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DiscoveryProfileDB(SQLModel, table=True):
    __tablename__ = "discovery_profiles"

    id: str = Field(primary_key=True)
    profile_type: str = Field(default="job_search")
    name: str
    enabled: bool = Field(default=True)
    config_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DiscoveryRunDB(SQLModel, table=True):
    __tablename__ = "discovery_runs"

    id: str = Field(primary_key=True)
    profile_id: str = Field(index=True)
    status: str = Field(default="running")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    result_count: int = Field(default=0)
    error_count: int = Field(default=0)
    errors_json: str = Field(default="[]")
    trace_id: Optional[str] = None


class DiscoveryResultDB(SQLModel, table=True):
    __tablename__ = "discovery_results"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    result_type: str
    title: str
    source_url: Optional[str] = None
    source_name: str
    raw_text: Optional[str] = None
    normalized_payload_json: str = Field(default="{}")
    fingerprint: Optional[str] = None
    duplicate_of: Optional[str] = None
    is_duplicate: bool = Field(default=False)
    ignored: bool = Field(default=False)
    converted_to_case_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CaseAgentRunDB(SQLModel, table=True):
    __tablename__ = "case_agent_runs"

    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    agent_profile_id: str
    input_artifact_ids_json: str = Field(default="[]")
    output_artifact_ids_json: str = Field(default="[]")
    status: str = Field(default="running")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    trace_id: Optional[str] = None
    model_profile_id: Optional[str] = None
    estimated_cost: Optional[float] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    metadata_json: str = Field(default="{}")


class CaseBlueprintBindingDB(SQLModel, table=True):
    __tablename__ = "case_blueprint_bindings"

    id: str = Field(primary_key=True)
    case_id: str = Field(index=True)
    visual_process_graph_id: str
    blueprint_id: Optional[str] = None
    active_step_id: Optional[str] = None
    workflow_id: Optional[str] = None
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata_json: str = Field(default="{}")
