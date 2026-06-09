from __future__ import annotations

import time
from typing import List, Optional

from sqlmodel import JSON, Column, Field, SQLModel


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
    status_reason_code: Optional[str] = None
    status_reason_details: dict = Field(default={}, sa_column=Column(JSON))
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
    status_reason_code: Optional[str] = None
    status_reason_details: dict = Field(default={}, sa_column=Column(JSON))
    parent_task_id: Optional[str] = None
    source_task_id: Optional[str] = None
    derivation_reason: Optional[str] = None
    derivation_depth: int = 0
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))


class ConfigDB(SQLModel, table=True):
    __tablename__ = "config"
    key: str = Field(primary_key=True)
    value_json: str
