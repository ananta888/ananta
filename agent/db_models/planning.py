from __future__ import annotations

import time
import uuid
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


class TemplateDB(SQLModel, table=True):
    __tablename__ = "templates"
    __table_args__ = (sa.UniqueConstraint("name", name="uq_templates_name"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    prompt_template: str
    is_seed: bool = Field(default=False, sa_column=sa.Column(sa.Boolean, nullable=False, server_default=sa.false()))


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
    mode: str = Field(default="generic", index=True)
    mode_data: dict = Field(default={}, sa_column=Column(JSON))
    planning_lease_expires_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlaybookDB(SQLModel, table=True):
    __tablename__ = "playbooks"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    tasks: List[dict] = Field(default=[], sa_column=Column(JSON))
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


class PlanningRunDB(SQLModel, table=True):
    __tablename__ = "planning_runs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_text_hash: Optional[str] = None
    goal_text_preview: Optional[str] = None
    mode: str = Field(default="generic", index=True)
    mode_data: dict = Field(default={}, sa_column=Column(JSON))
    model_provider: Optional[str] = Field(default=None, index=True)
    model_name: Optional[str] = Field(default=None, index=True)
    model_base_url_hash: Optional[str] = None
    planning_profile: Optional[str] = Field(default=None, index=True)
    prompt_version_id: Optional[str] = Field(default=None, index=True)
    prompt_language: Optional[str] = None
    context_policy_ref: Optional[str] = None
    context_char_count: int = 0
    raw_output_ref: Optional[str] = None
    raw_output_preview: Optional[str] = None
    parse_mode: Optional[str] = None
    parse_confidence: Optional[str] = None
    parse_warnings: list[str] = Field(default=[], sa_column=Column(JSON))
    repair_needed: bool = False
    repair_success: bool = False
    repair_strategy_used: Optional[str] = None
    repair_attempt_count: int = 0
    validation_success: bool = False
    validation_errors: list[str] = Field(default=[], sa_column=Column(JSON))
    generated_task_count: int = 0
    expected_artifacts_count: int = 0
    verification_spec_count: int = 0
    dependency_mode_distribution: dict = Field(default={}, sa_column=Column(JSON))
    materialized_task_ids: list[str] = Field(default=[], sa_column=Column(JSON))
    status: str = Field(default="started", index=True)
    error_classification: Optional[str] = None
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


class PlanningPromptVersionDB(SQLModel, table=True):
    __tablename__ = "planning_prompt_versions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    version: str = Field(index=True)
    language: str = Field(default="de", index=True)
    target_model_family: Optional[str] = Field(default=None, index=True)
    mode: str = Field(default="generic", index=True)
    output_contract: dict = Field(default={}, sa_column=Column(JSON))
    system_rules: list[str] = Field(default=[], sa_column=Column(JSON))
    user_prompt_template: str
    repair_prompt_template: Optional[str] = None
    checksum: str = Field(index=True)
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanningModelProfileDB(SQLModel, table=True):
    __tablename__ = "planning_model_profiles"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    provider: str = Field(index=True)
    model_name_pattern: Optional[str] = Field(default=None, index=True)
    model_family: Optional[str] = Field(default=None, index=True)
    profile_name: str = Field(index=True)
    prompt_language: str = "de"
    context_max_chars: int = 1200
    max_output_tokens: int = 1024
    temperature: float = 0.2
    repair_attempts: int = 2
    repair_strategies: list[dict] = Field(default=[], sa_column=Column(JSON))
    preferred_prompt_version_id: Optional[str] = None
    output_contract_strictness: str = "repair_required"
    supports_json_mode: bool = False
    requires_english_prompt: bool = False
    learning_state: dict = Field(default={}, sa_column=Column(JSON))
    notes: Optional[str] = None
    enabled: bool = True
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanningEvaluationDB(SQLModel, table=True):
    __tablename__ = "planning_evaluations"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    planning_run_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    parse_score: float = 0.0
    validation_score: float = 0.0
    materialization_score: float = 0.0
    execution_score: float = 0.0
    artifact_score: float = 0.0
    verification_score: float = 0.0
    total_score: float = 0.0
    completion_status: str = Field(default="pending", index=True)
    failure_reason: Optional[str] = None
    details: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanningTemplateCandidateDB(SQLModel, table=True):
    __tablename__ = "planning_template_candidates"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    source_run_id: Optional[str] = Field(default=None, index=True)
    goal_type: Optional[str] = Field(default=None, index=True)
    mode: str = Field(default="generic", index=True)
    candidate_payload: dict = Field(default={}, sa_column=Column(JSON))
    confidence: str = Field(default="low", index=True)
    status: str = Field(default="proposed", index=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanningPatternClusterDB(SQLModel, table=True):
    __tablename__ = "planning_pattern_clusters"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    goal_type: Optional[str] = Field(default=None, index=True)
    model_provider: Optional[str] = Field(default=None, index=True)
    model_name: Optional[str] = Field(default=None, index=True)
    cluster_key: str = Field(index=True)
    cluster_payload: dict = Field(default={}, sa_column=Column(JSON))
    sample_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class PlanningReviewItemDB(SQLModel, table=True):
    __tablename__ = "planning_review_items"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    planning_run_id: str = Field(index=True)
    review_type: str = Field(index=True)
    status: str = Field(default="open", index=True)
    reason_codes: List[str] = Field(default=[], sa_column=Column(JSON))
    action_log: List[dict] = Field(default=[], sa_column=Column(JSON))
    payload: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
