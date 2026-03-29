from __future__ import annotations

import time
from typing import Any

from agent.db_models import PolicyDecisionDB, TaskDB, VerificationRecordDB
from agent.models import HubEventCatalogContract, HubEventContextContract, HubEventContract

TASK_HISTORY_EVENT_TYPES = [
    "task_created",
    "task_activity",
    "task_ingested",
    "task_claimed",
    "task_assigned",
    "task_delegated",
    "task_completed_with_gates",
    "task_materialized_from_plan",
    "task_verification_updated",
    "task_intervention",
    "task_handoff",
    "proposal_snapshot",
    "proposal_result",
    "proposal_review",
    "execution_result",
    "trigger_created",
    "dependency_unblocked",
    "dependency_failed",
    "dependency_blocked",
]

GOVERNANCE_EVENT_TYPES = [
    "policy_decision_recorded",
    "verification_record_updated",
]


def build_hub_event(
    *,
    channel: str,
    event_type: str,
    timestamp: float | None = None,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    task_id: str | None = None,
    goal_id: str | None = None,
    trace_id: str | None = None,
    plan_id: str | None = None,
    verification_record_id: str | None = None,
) -> dict[str, Any]:
    event = HubEventContract(
        channel=channel,
        event_type=event_type,
        timestamp=timestamp if timestamp is not None else time.time(),
        actor=actor,
        context=HubEventContextContract(
            task_id=task_id,
            goal_id=goal_id,
            trace_id=trace_id,
            plan_id=plan_id,
            verification_record_id=verification_record_id,
        ),
        details=dict(details or {}),
    )
    return event.model_dump(exclude_none=True)


def build_hub_event_catalog() -> HubEventCatalogContract:
    return HubEventCatalogContract(
        channels={
            "task_history": TASK_HISTORY_EVENT_TYPES,
            "audit": ["*"],  # AuditLogDB.action is the canonical event_type source.
            "governance": GOVERNANCE_EVENT_TYPES,
        },
        notes={
            "audit": "For audit events, `AuditLogDB.action` is the canonical event_type and the envelope is stored in `details._event`.",
            "governance": "Governance summaries expose versioned envelope metadata and optional latest event envelopes for policy/verification entries.",
        },
    )


def build_task_history_event(
    task: TaskDB,
    event_type: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    timestamp: float | None = None,
) -> dict[str, Any]:
    return build_hub_event(
        channel="task_history",
        event_type=event_type,
        timestamp=timestamp,
        actor=actor,
        details=details,
        task_id=task.id,
        goal_id=task.goal_id,
        trace_id=task.goal_trace_id,
        plan_id=task.plan_id,
    )


def build_policy_governance_event(record: PolicyDecisionDB) -> dict[str, Any]:
    return build_hub_event(
        channel="governance",
        event_type="policy_decision_recorded",
        timestamp=record.created_at,
        actor=record.policy_name,
        details={
            "policy_decision_id": record.id,
            "decision_type": record.decision_type,
            "status": record.status,
            "policy_name": record.policy_name,
            "policy_version": record.policy_version,
            "worker_url": record.worker_url,
            "reasons": list(record.reasons or []),
            "details": dict(record.details or {}),
        },
        task_id=record.task_id,
        goal_id=record.goal_id,
        trace_id=record.trace_id,
    )


def build_verification_governance_event(record: VerificationRecordDB) -> dict[str, Any]:
    return build_hub_event(
        channel="governance",
        event_type="verification_record_updated",
        timestamp=record.updated_at,
        actor="verification_service",
        details={
            "verification_record_id": record.id,
            "verification_type": record.verification_type,
            "status": record.status,
            "retry_count": record.retry_count,
            "repair_attempts": record.repair_attempts,
            "escalation_reason": record.escalation_reason,
        },
        task_id=record.task_id,
        goal_id=record.goal_id,
        trace_id=record.trace_id,
        verification_record_id=record.id,
    )
