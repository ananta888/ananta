"""Pure rules for the Hub task projection of bound (v2) knowledge-index jobs.

The Hub task row is a queue projection of an admitted execution record.
These functions build that projection, compare it with the record and
shape the terminal expired-dispatch marker.  They perform no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_job_contract import (
    KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA,
    task_mapping,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
)

EXPIRED_DISPATCH_RECONCILIATION_SCHEMA = (
    "ananta.knowledge_index_dispatch_reconciliation.v1"
)


def bound_task_ingest_request(
    *,
    record: Any,
    assigned_worker_url: str,
    destination_selection: Mapping[str, Any],
    source_access_intent: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the ``ingest_task`` keyword arguments for one admitted record."""

    envelope = record.job.to_wire()
    return {
        "task_id": record.job.job_id,
        "status": "assigned",
        "title": (
            f"Knowledge index revision: {record.job.scope_id}"
        )[:200],
        "description": (
            "Hub-authorized immutable source-revision index job."
        ),
        "priority": "medium",
        "created_by": record.job.created_by,
        "source": "knowledge_index",
        "team_id": record.job.authority_binding.project_id,
        "tags": [
            "knowledge_index",
            "hub_delegated",
            "source_revision_bound",
        ],
        "event_type": "task_ingested",
        "event_channel": "hub_task_queue",
        "event_details": {
            "domain_event_type": (
                "knowledge_index_execution_authorized"
            ),
            "authority_binding_digest": envelope[
                "authority_binding"
            ]["binding_digest"],
        },
        "extra_fields": {
            "task_kind": "codecompass_index_build",
            "retrieval_intent": "index_snapshot",
            "required_context_scope": record.job.source_scope,
            "required_capabilities": [
                "retrieval",
                "index_write",
            ],
            "assigned_agent_url": assigned_worker_url,
            "worker_execution_context": {
                "knowledge_index_job": envelope,
                "destination_selection": dict(
                    destination_selection
                ),
                "source_access_intent": dict(
                    source_access_intent
                ),
            },
            "verification_spec": {
                "schema": (
                    KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA
                ),
                "artifact_first": True,
                "authority_binding_digest": envelope[
                    "authority_binding"
                ]["binding_digest"],
                "file_manifest_digest": envelope[
                    "file_manifest"
                ]["manifest_digest"],
            },
        },
    }


def validate_bound_task_projection(
    task: Any,
    *,
    record: Any,
    assigned_worker_url: str,
    destination_selection: Mapping[str, Any],
    source_access_intent: Mapping[str, Any],
) -> None:
    raw = task_mapping(task)
    context = dict(raw.get("worker_execution_context") or {})
    projected_envelope = dict(
        context.get("knowledge_index_job") or {}
    )
    projected_envelope.pop(
        "source_access_enforcement_manifest",
        None,
    )
    expected_worker_url = str(assigned_worker_url).rstrip("/")
    projected_worker_url = str(
        raw.get("assigned_agent_url") or ""
    ).rstrip("/")
    if (
        str(raw.get("task_kind") or "")
        != "codecompass_index_build"
        or projected_worker_url != expected_worker_url
        or projected_envelope != record.job.to_wire()
        or dict(context.get("destination_selection") or {})
        != dict(destination_selection)
        or dict(context.get("source_access_intent") or {})
        != dict(source_access_intent)
    ):
        raise RuntimeError(
            "knowledge_index_execution_queue_projection_conflict"
        )


def task_matches_bound_execution(
    task: Any,
    *,
    expected_envelope: Mapping[str, Any],
) -> bool:
    raw = task_mapping(task)
    envelope = dict(
        dict(raw.get("worker_execution_context") or {}).get(
            "knowledge_index_job"
        )
        or {}
    )
    envelope.pop("source_access_enforcement_manifest", None)
    return envelope == dict(expected_envelope)


def expired_dispatch_reconciliation_marker(record: Any) -> dict[str, Any]:
    assignment = record.job.assignment
    return {
        "schema": EXPIRED_DISPATCH_RECONCILIATION_SCHEMA,
        "job_id": record.job.job_id,
        "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
        "authority_binding_digest": (
            record.job.authority_binding.binding_digest
        ),
        "assignment_id": assignment.assignment_id,
        "lease_id": assignment.lease_id,
        "lease_generation": assignment.lease_generation,
        "lease_expires_epoch_ms": assignment.lease_expires_epoch_ms,
        "execution_lock_version": int(record.lock_version),
        "reconciled_at_epoch_ms": int(record.completed_at_epoch_ms),
    }


def project_expired_dispatch_failure(
    task: Any,
    *,
    marker: Mapping[str, Any],
) -> None:
    raw = task_mapping(task)
    details = dict(raw.get("status_reason_details") or {})
    details["knowledge_index_dispatch_reconciliation"] = dict(marker)
    if isinstance(task, dict):
        task["status_reason_code"] = (
            KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON
        )
        task["status_reason_details"] = details
        return
    setattr(
        task,
        "status_reason_code",
        KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
    )
    setattr(task, "status_reason_details", details)


def task_has_expired_dispatch_projection(
    task: Any,
    *,
    marker: Mapping[str, Any],
) -> bool:
    raw = task_mapping(task)
    details = dict(raw.get("status_reason_details") or {})
    return bool(
        str(raw.get("status") or "").strip().lower() == "failed"
        and str(raw.get("status_reason_code") or "")
        == KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON
        and details.get("knowledge_index_dispatch_reconciliation")
        == dict(marker)
    )


__all__ = [
    "EXPIRED_DISPATCH_RECONCILIATION_SCHEMA",
    "bound_task_ingest_request",
    "expired_dispatch_reconciliation_marker",
    "project_expired_dispatch_failure",
    "task_has_expired_dispatch_projection",
    "task_matches_bound_execution",
    "validate_bound_task_projection",
]
