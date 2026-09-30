"""Pure Hub contract for delegated knowledge-index jobs.

Schemas, limits, canonical encoding, Hub-owned graph intent normalization,
legacy result validation and the read-only job view projection.  Nothing in
this module performs I/O; ``KnowledgeIndexJobService`` composes it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_worker_result_references import (
    validate_worker_artifact_references,
)
from ananta_contracts.knowledge_index_execution import (
    MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES,
)

KNOWLEDGE_INDEX_JOB_SCHEMA = "ananta.knowledge_index_job.v1"
KNOWLEDGE_INDEX_RESULT_SCHEMA = "ananta.knowledge_index_job_result.v1"
KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA = (
    "ananta.knowledge_index_execution_result.v2"
)
INLINE_JOB_PAYLOAD_BYTES = 128 * 1024
MAX_JOB_PAYLOAD_BYTES = MAX_KNOWLEDGE_INDEX_PAYLOAD_BYTES
PAYLOAD_MEDIA_TYPE = "application/vnd.ananta.knowledge-index-job+json"
GRAPH_VISUAL_OPTIONS_SCHEMA = "codecompass_graph_visual_options.v1"
RECONCILABLE_TASK_STATUSES = {
    "created",
    "todo",
    "blocked",
    "blocked_by_dependency",
    "proposing",
    "assigned",
    "in_progress",
    "running",
}
MAX_GRAPH_BLAST_RADIUS_SEEDS = 256
MAX_GRAPH_SEED_ID_LENGTH = 512
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
WORKER_RESULT_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "idempotency_fingerprint",
        "status",
        "reason_code",
        "knowledge_index",
        "run",
        "results",
        "artifact_refs",
        "error",
    }
)
_TASK_STATUS_TO_JOB_STATUS = {
    "created": "queued",
    "todo": "queued",
    "blocked": "queued",
    "blocked_by_dependency": "queued",
    "proposing": "queued",
    "assigned": "running",
    "in_progress": "running",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def task_mapping(task: Any) -> dict[str, Any]:
    if hasattr(task, "model_dump"):
        return dict(task.model_dump())
    if isinstance(task, Mapping):
        return dict(task)
    return dict(vars(task))


def normalize_graph_visual_metrics_options(
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize the Hub-owned intent without importing worker algorithms."""

    if raw is not None and not isinstance(raw, Mapping):
        raise ValueError("graph_visual_options_invalid")
    options = dict(raw or {})
    allowed = {"schema", "include_advanced_metrics", "blast_radius_seeds"}
    if set(options) - allowed:
        raise ValueError("graph_visual_options_fields_unknown")
    schema = str(options.get("schema") or GRAPH_VISUAL_OPTIONS_SCHEMA)
    if schema != GRAPH_VISUAL_OPTIONS_SCHEMA:
        raise ValueError("graph_visual_options_schema_invalid")
    include_advanced = options.get("include_advanced_metrics", True)
    if not isinstance(include_advanced, bool):
        raise ValueError("graph_visual_options_advanced_metrics_invalid")
    raw_seeds = options.get("blast_radius_seeds", [])
    if not isinstance(raw_seeds, list) or len(raw_seeds) > MAX_GRAPH_BLAST_RADIUS_SEEDS:
        raise ValueError("graph_visual_options_blast_seeds_invalid")
    seeds: set[str] = set()
    for raw_seed in raw_seeds:
        if not isinstance(raw_seed, str):
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seed = raw_seed.strip()
        if not seed or len(seed) > MAX_GRAPH_SEED_ID_LENGTH:
            raise ValueError("graph_visual_options_blast_seed_invalid")
        seeds.add(seed)
    return {
        "schema": GRAPH_VISUAL_OPTIONS_SCHEMA,
        "include_advanced_metrics": include_advanced,
        "blast_radius_seeds": sorted(seeds),
    }


def validate_legacy_worker_result(
    *,
    job_id: str,
    result: Mapping[str, Any] | None,
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one ``ananta.knowledge_index_job_result.v1`` Worker result."""

    payload = dict(result or {})
    missing = WORKER_RESULT_FIELDS - set(payload)
    unknown = set(payload) - WORKER_RESULT_FIELDS
    if missing:
        raise ValueError("knowledge_index_result_fields_missing")
    if unknown:
        raise ValueError("knowledge_index_result_fields_unknown")
    if str(payload.get("schema") or "") != KNOWLEDGE_INDEX_RESULT_SCHEMA:
        raise ValueError("knowledge_index_result_schema_invalid")
    if str(payload.get("job_id") or "") != str(job_id):
        raise ValueError("knowledge_index_result_job_mismatch")
    if str(envelope.get("job_id") or "") != str(job_id):
        raise ValueError("knowledge_index_job_binding_mismatch")
    result_fingerprint = str(payload.get("idempotency_fingerprint") or "")
    if result_fingerprint != str(envelope.get("idempotency_fingerprint") or ""):
        raise ValueError("knowledge_index_result_fingerprint_mismatch")
    if len(result_fingerprint) != 64 or any(char not in "0123456789abcdef" for char in result_fingerprint):
        raise ValueError("knowledge_index_result_fingerprint_invalid")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise ValueError("knowledge_index_result_status_invalid")
    if payload.get("reason_code") is not None and not isinstance(payload.get("reason_code"), str):
        raise ValueError("knowledge_index_result_reason_code_invalid")
    for field in ("knowledge_index", "run"):
        value = payload.get(field)
        if value is not None and not isinstance(value, Mapping):
            raise ValueError(f"knowledge_index_result_{field}_invalid")
    results = payload.get("results")
    if results is not None and (
        not isinstance(results, list)
        or any(not isinstance(item, Mapping) for item in results)
    ):
        raise ValueError("knowledge_index_result_results_invalid")
    artifact_refs = payload.get("artifact_refs")
    if not isinstance(artifact_refs, list):
        raise ValueError("knowledge_index_result_artifact_refs_invalid")
    validate_worker_artifact_references(artifact_refs)
    if payload.get("error") is not None and not isinstance(payload.get("error"), str):
        raise ValueError("knowledge_index_result_error_invalid")
    return {
        **payload,
        "status": status,
        "knowledge_index": dict(payload["knowledge_index"])
        if isinstance(payload.get("knowledge_index"), Mapping)
        else None,
        "run": dict(payload["run"]) if isinstance(payload.get("run"), Mapping) else None,
        "results": [dict(item) for item in results] if isinstance(results, list) else None,
        "artifact_refs": [dict(item) for item in artifact_refs],
    }


def legacy_job_ingest_request(
    *,
    job_id: str,
    job_type: str,
    scope_id: str,
    source_scope: str | None,
    profile_name: str | None,
    created_by: str | None,
    created_at: float,
    idempotency_fingerprint: str,
    payload: Mapping[str, Any],
    worker_payload: Mapping[str, Any],
    payload_artifact_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the ``ingest_task`` keyword arguments for one v1 job."""

    envelope = {
        "schema": KNOWLEDGE_INDEX_JOB_SCHEMA,
        "job_id": job_id,
        "job_type": job_type,
        "scope_id": scope_id,
        "source_scope": source_scope,
        "profile_name": str(profile_name or "default"),
        "created_by": created_by,
        "created_at": created_at,
        "idempotency_fingerprint": idempotency_fingerprint,
        "record_count": len(list(payload.get("records") or [])),
        "artifact_ids": list(payload.get("artifact_ids") or []),
        "payload": worker_payload,
    }
    return dict(
        task_id=job_id,
        status="todo",
        title=f"Knowledge index: {job_type} {scope_id}"[:200],
        description="Worker-delegated persistent CodeCompass indexing job.",
        priority="medium",
        created_by=created_by or "knowledge-index-api",
        source="knowledge_index",
        tags=["knowledge_index", "hub_delegated", "persistent_job"],
        event_type="task_ingested",
        event_channel="hub_task_queue",
        event_details={
            "job_type": job_type,
            "scope_id": scope_id,
            "idempotency_fingerprint": idempotency_fingerprint,
            "domain_event_type": "knowledge_index_job_queued",
        },
        extra_fields={
            "task_kind": "codecompass_index_build",
            "retrieval_intent": "index_snapshot",
            "required_context_scope": source_scope or "artifact",
            "required_capabilities": ["retrieval", "index_write"],
            "worker_execution_context": {"knowledge_index_job": envelope},
            "verification_spec": {
                "schema": KNOWLEDGE_INDEX_RESULT_SCHEMA,
                "artifact_first": True,
                "idempotency_fingerprint": idempotency_fingerprint,
                "payload_artifact_ref": payload_artifact_ref,
            },
        },
    )


def project_job_view(
    raw: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    """Project the queue-facing job view from one Hub task row."""

    task_status = str(raw.get("status") or "todo").strip().lower()
    # Proposal is still queue-facing work. The independent execution
    # projection remains authoritative when its binding is already
    # running, so callers can observe both facts without a read-side write.
    status = _TASK_STATUS_TO_JOB_STATUS.get(task_status, "queued")
    return {
        "job_id": str(raw.get("id") or envelope.get("job_id") or ""),
        "job_type": envelope.get("job_type"),
        "scope_id": envelope.get("scope_id"),
        "source_scope": envelope.get("source_scope"),
        "status": status,
        "phase": "completed" if status == "completed" else "failed" if status == "failed" else status,
        "progress_percent": 100 if status in TERMINAL_STATUSES else 10 if status == "running" else 0,
        "created_by": envelope.get("created_by"),
        "profile_name": envelope.get("profile_name"),
        "created_at": raw.get("created_at", envelope.get("created_at")),
        "updated_at": raw.get("updated_at"),
        "record_count": envelope.get("record_count"),
        "artifact_ids": envelope.get("artifact_ids"),
        "idempotency_fingerprint": envelope.get("idempotency_fingerprint"),
        "task_kind": raw.get("task_kind"),
        "reason_code": raw.get("status_reason_code"),
        "tenant_id": (envelope.get("authority_binding") or {}).get(
            "tenant_id"
        ),
        "project_id": (envelope.get("authority_binding") or {}).get(
            "project_id"
        ),
        "source_revision_id": (
            envelope.get("authority_binding") or {}
        ).get("source_revision_id"),
        "file_manifest_digest": (
            envelope.get("file_manifest") or {}
        ).get("manifest_digest"),
        "assignment_id": (envelope.get("assignment") or {}).get(
            "assignment_id"
        ),
    }


__all__ = [
    "KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_EXECUTION_RESULT_SCHEMA",
    "KNOWLEDGE_INDEX_JOB_SCHEMA",
    "KNOWLEDGE_INDEX_RESULT_SCHEMA",
    "canonical_json",
    "fingerprint",
    "legacy_job_ingest_request",
    "normalize_graph_visual_metrics_options",
    "project_job_view",
    "task_mapping",
    "validate_legacy_worker_result",
]
