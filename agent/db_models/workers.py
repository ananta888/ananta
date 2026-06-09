from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlmodel import JSON, Column, Field, SQLModel


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
    selected_worker_id: Optional[str] = Field(default=None, index=True)
    selected_worker_kind: Optional[str] = Field(default=None, index=True)
    selected_runtime_target_id: Optional[str] = Field(default=None, index=True)
    selected_runtime_kind: Optional[str] = Field(default=None, index=True)
    selection_mode: Optional[str] = Field(default=None)
    selection_decision_ref: Optional[str] = Field(default=None, index=True)
    slot_lease_id: Optional[str] = Field(default=None, index=True)
    queue_position: Optional[int] = Field(default=None)
    scheduled_ollama_endpoint: Optional[str] = Field(default=None, index=True)
    scheduled_ollama_model: Optional[str] = Field(default=None, index=True)
    parallel_group_id: Optional[str] = Field(default=None, index=True)
    scheduling_reason_code: Optional[str] = Field(default=None)
    scheduled_at: Optional[float] = Field(default=None)
    started_at: Optional[float] = Field(default=None)
    finished_at: Optional[float] = Field(default=None)
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
    actual_worker_id: Optional[str] = Field(default=None, index=True)
    actual_worker_kind: Optional[str] = Field(default=None, index=True)
    actual_runtime_target_id: Optional[str] = Field(default=None, index=True)
    actual_runtime_kind: Optional[str] = Field(default=None, index=True)
    selection_reason: Optional[str] = Field(default=None)
    created_at: float = Field(default_factory=time.time)


class WorkerSlotLeaseDB(SQLModel, table=True):
    __tablename__ = "worker_slot_leases"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    lease_type: str = Field(default="worker", index=True)
    status: str = Field(default="active", index=True)
    worker_id: Optional[str] = Field(default=None, index=True)
    worker_kind: Optional[str] = Field(default=None, index=True)
    runtime_target_id: Optional[str] = Field(default=None, index=True)
    runtime_kind: Optional[str] = Field(default=None, index=True)
    ollama_endpoint: Optional[str] = Field(default=None, index=True)
    ollama_model: Optional[str] = Field(default=None, index=True)
    parent_task_id: Optional[str] = Field(default=None, index=True)
    worker_job_id: Optional[str] = Field(default=None, index=True)
    queue_position: Optional[int] = Field(default=None)
    reason_code: Optional[str] = Field(default=None)
    acquired_at: float = Field(default_factory=time.time, index=True)
    deadline_at: float = Field(default_factory=lambda: time.time() + 600, index=True)
    released_at: Optional[float] = Field(default=None, index=True)
    lease_metadata: dict = Field(default={}, sa_column=Column(JSON))


class HeuristicDecisionLeaseDB(SQLModel, table=True):
    __tablename__ = "heuristic_decision_leases"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    heuristic_id: str = Field(index=True)
    version: str = Field(default="1.0.0")
    domain: str = Field(index=True)
    status: str = Field(default="active", index=True)
    selected_by: str = Field(default="heuristic_self")
    context_hash: str = Field(default="", index=True)
    ttl_seconds: float = Field(default=7.0)
    reason_codes: List[str] = Field(default=[], sa_column=Column(JSON))
    acquired_at: float = Field(default_factory=time.time, index=True)
    deadline_at: float = Field(default_factory=lambda: time.time() + 7, index=True)
    released_at: Optional[float] = Field(default=None, index=True)


class EvolutionRunDB(SQLModel, table=True):
    __tablename__ = "evolution_runs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    provider_name: str = Field(index=True)
    status: str = "completed"
    trigger_type: str = "manual"
    trigger_source: Optional[str] = None
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    plan_id: Optional[str] = Field(default=None, index=True)
    context_id: Optional[str] = Field(default=None, index=True)
    summary: Optional[str] = None
    context_refs: List[dict] = Field(default=[], sa_column=Column(JSON))
    result_metadata: dict = Field(default={}, sa_column=Column(JSON))
    provider_metadata: dict = Field(default={}, sa_column=Column(JSON))
    raw_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class EvolutionProposalDB(SQLModel, table=True):
    __tablename__ = "evolution_proposals"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    run_id: str = Field(index=True)
    provider_name: str = Field(index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    proposal_type: str = "improvement"
    title: str
    description: str
    rationale: Optional[str] = None
    risk_level: str = "unknown"
    confidence: Optional[float] = None
    requires_review: bool = True
    status: str = "proposed"
    target_refs: List[dict] = Field(default=[], sa_column=Column(JSON))
    artifact_refs: List[dict] = Field(default=[], sa_column=Column(JSON))
    proposal_metadata: dict = Field(default={}, sa_column=Column(JSON))
    provider_metadata: dict = Field(default={}, sa_column=Column(JSON))
    raw_payload: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)
