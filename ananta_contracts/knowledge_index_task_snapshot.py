"""Capability-free Hub snapshot for an isolated knowledge-index Worker."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.knowledge_index_execution import (
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    parse_execution_job,
)

KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCHEMA = "ananta.knowledge_index_task_snapshot.v1"
KNOWLEDGE_INDEX_WORKER_BINDING_SCHEMA = "ananta.knowledge_index_worker_binding.v1"
# File paths are contract-bounded to 512 UTF-8 bytes. The maximum 20k-entry
# manifest therefore serializes below this cap when the wire keeps UTF-8
# characters unescaped. Source contents remain artifact-first and separate.
MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES = 16 * 1024 * 1024
SOURCE_ACCESS_MANIFEST_FIELD = "source_access_enforcement_manifest"

_SNAPSHOT_FIELDS = frozenset({"schema", "task"})
_TASK_FIELDS = frozenset({"id", "status", "task_kind", "worker_execution_context"})
_CONTEXT_FIELDS = frozenset({"knowledge_index_job", "knowledge_index_worker_binding"})
_WORKER_BINDING_FIELDS = frozenset({"schema", "worker_id", "worker_url"})
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "verification_failed",
        "skipped",
        "aborted",
        "timeout",
        "archived",
    }
)


class KnowledgeIndexTaskSnapshotContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeIndexTaskSnapshot:
    task_id: str
    status: str
    job: Mapping[str, Any]
    worker_id: str
    worker_url: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "schema": KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCHEMA,
            "task": {
                "id": self.task_id,
                "status": self.status,
                "task_kind": "codecompass_index_build",
                "worker_execution_context": {
                    "knowledge_index_job": dict(self.job),
                    "knowledge_index_worker_binding": {
                        "schema": KNOWLEDGE_INDEX_WORKER_BINDING_SCHEMA,
                        "worker_id": self.worker_id,
                        "worker_url": self.worker_url,
                    },
                },
            },
        }


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_invalid") from exc
    if len(encoded) > MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_too_large")
    return encoded


def parse_knowledge_index_task_snapshot(
    value: Mapping[str, Any] | None,
    *,
    expected_job_id: str,
    expected_worker_id: str,
    expected_worker_url: str,
    now_epoch_ms: int | None = None,
) -> KnowledgeIndexTaskSnapshot:
    if not isinstance(value, Mapping):
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_invalid")
    snapshot = dict(value)
    _canonical_bytes(snapshot)
    task = snapshot.get("task")
    if (
        set(snapshot) != _SNAPSHOT_FIELDS
        or snapshot.get("schema") != KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCHEMA
        or not isinstance(task, Mapping)
        or set(task) != _TASK_FIELDS
        or task.get("task_kind") != "codecompass_index_build"
    ):
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_invalid")
    task_id = str(task.get("id") or "").strip()
    status = str(task.get("status") or "").strip().lower()
    if task_id != str(expected_job_id or "").strip() or not status or status in _TERMINAL_STATUSES:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_task_mismatch")
    context = task.get("worker_execution_context")
    if not isinstance(context, Mapping) or set(context) != _CONTEXT_FIELDS:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_context_invalid")
    raw_job = context.get("knowledge_index_job")
    if (
        not isinstance(raw_job, Mapping)
        or raw_job.get("schema") != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
        or SOURCE_ACCESS_MANIFEST_FIELD in raw_job
    ):
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_job_invalid")
    try:
        parsed_job = parse_execution_job(raw_job)
    except Exception as exc:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_job_invalid") from exc
    if parsed_job.job_id != task_id:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_job_mismatch")
    binding = context.get("knowledge_index_worker_binding")
    if (
        not isinstance(binding, Mapping)
        or set(binding) != _WORKER_BINDING_FIELDS
        or binding.get("schema") != KNOWLEDGE_INDEX_WORKER_BINDING_SCHEMA
    ):
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_worker_invalid")
    worker_id = str(binding.get("worker_id") or "").strip()
    worker_url = str(binding.get("worker_url") or "").strip().rstrip("/")
    expected_id = str(expected_worker_id or "").strip()
    expected_url = str(expected_worker_url or "").strip().rstrip("/")
    if (
        not worker_id
        or not worker_url
        or worker_id != expected_id
        or worker_url != expected_url
        or parsed_job.assignment.worker_id != worker_id
    ):
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_worker_mismatch")
    if now_epoch_ms is not None and int(now_epoch_ms) >= parsed_job.assignment.lease_expires_epoch_ms:
        raise KnowledgeIndexTaskSnapshotContractError("knowledge_index_task_snapshot_lease_stale")
    return KnowledgeIndexTaskSnapshot(
        task_id=task_id,
        status=status,
        job=parsed_job.to_wire(),
        worker_id=worker_id,
        worker_url=worker_url,
    )


def build_knowledge_index_task_snapshot(
    *,
    status: str,
    job: Mapping[str, Any],
    worker_id: str,
    worker_url: str,
) -> dict[str, Any]:
    task_id = str(job.get("job_id") or "").strip()
    snapshot = KnowledgeIndexTaskSnapshot(
        task_id=task_id,
        status=str(status or "").strip().lower(),
        job=dict(job),
        worker_id=str(worker_id or "").strip(),
        worker_url=str(worker_url or "").strip().rstrip("/"),
    ).to_wire()
    parse_knowledge_index_task_snapshot(
        snapshot,
        expected_job_id=task_id,
        expected_worker_id=worker_id,
        expected_worker_url=worker_url,
    )
    return snapshot


__all__ = [
    "KNOWLEDGE_INDEX_TASK_SNAPSHOT_SCHEMA",
    "KNOWLEDGE_INDEX_WORKER_BINDING_SCHEMA",
    "MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES",
    "KnowledgeIndexTaskSnapshot",
    "KnowledgeIndexTaskSnapshotContractError",
    "build_knowledge_index_task_snapshot",
    "parse_knowledge_index_task_snapshot",
]
