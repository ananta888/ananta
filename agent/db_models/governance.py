from __future__ import annotations

import time
import uuid
from typing import List, Optional

import sqlalchemy as sa
from sqlmodel import JSON, Column, Field, SQLModel


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


APPROVAL_REQUEST_STATUSES = ("pending", "granted", "denied", "expired", "consumed", "superseded")


class ApprovalRequestDB(SQLModel, table=True):
    """ALWA-001: persistent, digest-bound approval request per tool/mutation call.

    ``canonical_arguments`` is content-free: content-bearing fields are
    replaced by their content hash (ALWA-DD-007); the raw payload lives
    behind ``content_artifact_ref`` and is verified against
    ``content_hash`` before any re-execution. ``scope`` must never carry
    raw prompts or file contents.
    """

    __tablename__ = "approval_requests"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True)
    goal_id: Optional[str] = Field(default=None, index=True)
    trace_id: Optional[str] = Field(default=None, index=True)
    tool_name: str = Field(index=True)
    canonical_arguments: dict = Field(default={}, sa_column=Column(JSON))
    content_artifact_ref: Optional[str] = None
    content_hash: Optional[str] = None
    arguments_digest: str = Field(index=True)
    target_fingerprint: Optional[str] = Field(default=None, index=True)
    k_class: Optional[str] = None
    risk_class: str = Field(default="unknown", index=True)
    governance_mode: str = Field(default="balanced")
    status: str = Field(default="pending", index=True)
    scope: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    expires_at: Optional[float] = Field(default=None, index=True)
    decided_at: Optional[float] = None
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None
    consumed_at: Optional[float] = None


class ShareSessionDB(SQLModel, table=True):
    __tablename__ = "share_sessions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    owner_user_id: str = Field(index=True)
    owner_device_id: str = Field(index=True)
    title: str = "Shared Session"
    mode: str = Field(default="relay", index=True)
    transport: str = Field(default="hub_relay", index=True)
    permissions: dict = Field(default={}, sa_column=Column(JSON))
    invite_code: str = Field(index=True)
    expires_at: Optional[float] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    revoked_at: Optional[float] = Field(default=None, index=True)
    session_metadata: dict = Field(default={}, sa_column=Column(JSON))


class ShareParticipantDB(SQLModel, table=True):
    __tablename__ = "share_participants"
    __table_args__ = (sa.UniqueConstraint("session_id", "user_id", "device_id", name="uq_share_participant_identity"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(index=True, foreign_key="share_sessions.id")
    user_id: str = Field(index=True)
    device_id: str = Field(index=True)
    public_key_fingerprint: Optional[str] = Field(default=None, index=True)
    role: str = Field(default="participant", index=True)
    permissions: dict = Field(default={}, sa_column=Column(JSON))
    joined_at: float = Field(default_factory=time.time, index=True)
    revoked_at: Optional[float] = Field(default=None, index=True)
    participant_metadata: dict = Field(default={}, sa_column=Column(JSON))


class AgentSessionDB(SQLModel, table=True):
    __tablename__ = "agent_sessions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True)
    team_id: Optional[str] = Field(default=None, index=True)
    share_session_id: Optional[str] = Field(default=None, index=True)
    session_kind: str = Field(default="agent_execution", index=True)
    title: str = "Agent Session"
    mode: str = Field(default="relay", index=True)
    transport: str = Field(default="hub_relay", index=True)
    owner_user_id: str = Field(index=True)
    worker_id: Optional[str] = Field(default=None, index=True)
    worker_type: Optional[str] = None
    model: Optional[str] = None
    runtime: Optional[str] = None
    policy_snapshot_id: Optional[str] = Field(default=None, index=True)
    context_scope_id: Optional[str] = Field(default=None, index=True)
    permissions: dict = Field(default={}, sa_column=Column(JSON))
    status: str = Field(default="idle", index=True)
    failure_reason: Optional[str] = None
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    cancelled_at: Optional[float] = None
    expires_at: Optional[float] = None


class ToolCallDB(SQLModel, table=True):
    __tablename__ = "tool_calls"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    action_id: str = Field(index=True)
    tool_name: str = Field(index=True)
    arguments_preview: Optional[str] = None
    arguments_hash: Optional[str] = None
    target_path: Optional[str] = None
    status: str = Field(default="proposed", index=True)
    risk_level: str = Field(default="medium", index=True)
    policy_decision_id: Optional[str] = Field(default=None, index=True)
    created_at: float = Field(default_factory=time.time, index=True)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error_message: Optional[str] = None
    approved_by_user_id: Optional[str] = Field(default=None, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


class PolicySnapshotDB(SQLModel, table=True):
    __tablename__ = "policy_snapshots"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    session_id: str = Field(index=True)
    task_id: Optional[str] = Field(default=None, index=True)
    policy_version: str = Field(default="v1", index=True)
    risk_level: str = Field(default="medium", index=True)
    allowed_tools_json: List[str] = Field(default=[], sa_column=Column(JSON))
    denied_tools_json: List[str] = Field(default=[], sa_column=Column(JSON))
    allowed_paths_json: List[str] = Field(default=[], sa_column=Column(JSON))
    denied_paths_json: List[str] = Field(default=[], sa_column=Column(JSON))
    cloud_allowed: bool = False
    runtime_boundary: str = Field(default="local-only", index=True)
    requires_human_approval: bool = False
    approval_reason: Optional[str] = None
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time, index=True)


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
    escalation_code: Optional[str] = None
    escalation_details: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class ActionPackDB(SQLModel, table=True):
    __tablename__ = "action_packs"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    enabled: bool = True
    capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    policy_config: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RepairOutcomeMemoryDB(SQLModel, table=True):
    __tablename__ = "repair_outcome_memory"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    signature_id: str = Field(index=True)
    problem_class: str = Field(index=True)
    environment_facts: dict = Field(default={}, sa_column=Column(JSON))
    procedure_id: str
    execution_status: str
    outcome_label: str
    verification_evidence: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)


class RepairExecutionRecordDB(SQLModel, table=True):
    __tablename__ = "repair_execution_records"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    goal_id: str = ""
    task_id: str = Field(index=True)
    worker_job_id: str = ""
    plan_id: str = Field(index=True)
    procedure_id: str = Field(index=True)
    signature_id: str = ""
    problem_class: str = Field(index=True)
    platform_target: str = ""
    environment_facts_hash: str = ""
    execution_status: str = Field(index=True)
    outcome_label: str = ""
    verification_evidence_refs: list = Field(default=[], sa_column=Column(JSON))
    artifact_refs: list = Field(default=[], sa_column=Column(JSON))
    trace_ref: str = ""
    regression_flag: bool = False
    extra_metadata: dict = Field(default={}, sa_column=Column(JSON))
    selected_worker_id: Optional[str] = Field(default=None, index=True)
    selected_worker_kind: Optional[str] = Field(default=None, index=True)
    selected_runtime_target_id: Optional[str] = Field(default=None, index=True)
    selected_runtime_kind: Optional[str] = Field(default=None, index=True)
    actual_worker_id: Optional[str] = Field(default=None, index=True)
    actual_worker_kind: Optional[str] = Field(default=None, index=True)
    actual_runtime_target_id: Optional[str] = Field(default=None, index=True)
    actual_runtime_kind: Optional[str] = Field(default=None, index=True)
    selection_reason: Optional[str] = Field(default=None)
    selection_decision_ref: Optional[str] = Field(default=None, index=True)


class DecisionTraceDB(SQLModel, table=True):
    __tablename__ = "decision_traces"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    surface: str = Field(index=True)
    context_hash: str = Field(default="", index=True)
    lease_id: Optional[str] = Field(default=None, index=True)
    heuristic_id: Optional[str] = Field(default=None, index=True)
    strategy_id: Optional[str] = Field(default=None, index=True)
    rule_id: Optional[str] = Field(default=None, index=True)
    confidence: float = Field(default=0.0)
    fallback_reason: Optional[str] = Field(default=None, index=True)
    source: str = Field(default="heuristic", index=True)
    action_kind: str = Field(default="no_action", index=True)
    started_at: float = Field(default_factory=time.time, index=True)
    resolved_at: Optional[float] = Field(default=None, index=True)
    reason_codes: List[str] = Field(default=[], sa_column=Column(JSON))


class PairGroupDB(SQLModel, table=True):
    __tablename__ = "pair_groups"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    owner_user_id: str = Field(index=True)
    name: str = Field(index=True)
    description: str = Field(default="")
    default_permissions: dict = Field(default={}, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time, index=True)
    updated_at: float = Field(default_factory=time.time)


class PairGroupMemberDB(SQLModel, table=True):
    __tablename__ = "pair_group_members"
    __table_args__ = (sa.UniqueConstraint("group_id", "user_id", name="uq_pair_group_member"),)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    group_id: str = Field(index=True, foreign_key="pair_groups.id")
    user_id: str = Field(index=True)
    display_name: str = Field(default="")
    added_at: float = Field(default_factory=time.time, index=True)
