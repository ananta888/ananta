"""Pure digest and receipt rules for knowledge-index v2 executions.

The Hub execution binding service owns state transitions; this module owns
only the deterministic, side-effect free receipt shapes it persists: the
canonical result digest, the expired-dispatch tombstone and the closed
completion-projection payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent.models.knowledge_index_execution_binding import (
    KnowledgeIndexExecutionBindingError,
    KnowledgeIndexExecutionRecord,
)
from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
)

EXPIRED_DISPATCH_RECEIPT_SCHEMA = (
    "ananta.knowledge_index_execution_expired_dispatch.v1"
)
COMPLETION_PROJECTION_SCHEMA = (
    "ananta.knowledge_index.completion-projection.v1"
)
MAX_COMPLETION_PROJECTION_BYTES = 2 * 1024 * 1024


def execution_digest(value: object) -> str:
    """Return the canonical SHA-256 digest of a JSON-compatible value."""

    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def expired_dispatch_tombstone_digest(
    record: KnowledgeIndexExecutionRecord,
) -> str:
    job = record.job
    assignment = job.assignment
    return execution_digest(
        {
            "schema": EXPIRED_DISPATCH_RECEIPT_SCHEMA,
            "reason_code": KNOWLEDGE_INDEX_EXPIRED_DISPATCH_REASON,
            "job_id": job.job_id,
            "idempotency_fingerprint": job.idempotency_fingerprint,
            "authority_binding_digest": (
                job.authority_binding.binding_digest
            ),
            "assignment_id": assignment.assignment_id,
            "worker_id": assignment.worker_id,
            "lease_id": assignment.lease_id,
            "lease_generation": assignment.lease_generation,
            "lease_expires_epoch_ms": (
                assignment.lease_expires_epoch_ms
            ),
        }
    )


def is_expired_dispatch_tombstone(
    record: KnowledgeIndexExecutionRecord,
) -> bool:
    return (
        record.state == "failed"
        and record.result_digest
        == expired_dispatch_tombstone_digest(record)
    )


def completion_projection_candidate(
    *,
    job_id: str,
    worker_result: dict[str, Any],
    materialized_result: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    payload = {
        "schema": COMPLETION_PROJECTION_SCHEMA,
        "job_id": str(job_id),
        "worker_result_digest": execution_digest(worker_result),
        "materialized_result": dict(materialized_result),
        "artifact_references": [
            dict(item)
            for item in list(worker_result.get("artifact_refs") or [])
        ],
    }
    projection_digest = validate_completion_projection_payload(
        payload,
        job_id=str(job_id),
    )
    return payload, projection_digest


def validate_completion_projection_payload(
    payload: dict[str, Any],
    *,
    job_id: str,
) -> str:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "job_id",
        "worker_result_digest",
        "materialized_result",
        "artifact_references",
    }:
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_completion_projection_payload_invalid"
        )
    materialized = payload.get("materialized_result")
    references = payload.get("artifact_references")
    worker_result_digest = str(
        payload.get("worker_result_digest") or ""
    )
    if (
        payload.get("schema") != COMPLETION_PROJECTION_SCHEMA
        or str(payload.get("job_id") or "") != job_id
        or len(worker_result_digest) != 64
        or any(
            char not in "0123456789abcdef"
            for char in worker_result_digest
        )
        or not isinstance(materialized, dict)
        or str(materialized.get("status") or "") != "completed"
        or not isinstance(references, list)
        or len(references) > 6
        or any(not isinstance(item, dict) for item in references)
    ):
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_completion_projection_payload_invalid"
        )
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_completion_projection_payload_invalid"
        ) from exc
    if len(encoded) > MAX_COMPLETION_PROJECTION_BYTES:
        raise KnowledgeIndexExecutionBindingError(
            "knowledge_index_completion_projection_payload_too_large"
        )
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "COMPLETION_PROJECTION_SCHEMA",
    "EXPIRED_DISPATCH_RECEIPT_SCHEMA",
    "MAX_COMPLETION_PROJECTION_BYTES",
    "completion_projection_candidate",
    "execution_digest",
    "expired_dispatch_tombstone_digest",
    "is_expired_dispatch_tombstone",
    "validate_completion_projection_payload",
]
