"""Reserved generic-task boundary for Hub-governed knowledge indexing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

BOUND_KNOWLEDGE_INDEX_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON = (
    "knowledge_index_reserved_task_ingress_forbidden"
)
BOUND_KNOWLEDGE_INDEX_MUTATION_REASON = (
    "knowledge_index_task_control_plane_mutation_forbidden"
)
RESERVED_KNOWLEDGE_INDEX_TASK_KIND = "codecompass_index_build"
RESERVED_KNOWLEDGE_INDEX_TASK_SOURCE = "knowledge_index"
RESERVED_KNOWLEDGE_INDEX_CONTEXT_KEY = "knowledge_index_job"


@dataclass
class KnowledgeIndexTaskMutationConflict(RuntimeError):
    reason_code: str
    task_id: str
    action: str

    def __post_init__(self) -> None:
        RuntimeError.__init__(
            self,
            f"{self.reason_code}:{self.task_id}:{self.action}",
        )

    def as_data(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "task_id": self.task_id,
            "action": self.action,
            "http_status": 409,
        }


def _payload(task: Any) -> Mapping[str, Any]:
    if isinstance(task, Mapping):
        return task
    dump = getattr(task, "model_dump", None)
    return dump() if callable(dump) else {}


def find_reserved_knowledge_index_marker(
    payload: Mapping[str, Any] | None,
    *,
    source: Any = None,
) -> str | None:
    """Return the first knowledge-index-only generic-ingress marker."""

    values = payload if isinstance(payload, Mapping) else {}
    if any(
        str(candidate or "").strip().lower()
        == RESERVED_KNOWLEDGE_INDEX_TASK_SOURCE
        for candidate in (source, values.get("source"))
    ):
        return "source"
    if (
        str(values.get("task_kind") or "").strip().lower()
        == RESERVED_KNOWLEDGE_INDEX_TASK_KIND
    ):
        return "task_kind"
    worker_context = values.get("worker_execution_context")
    if (
        isinstance(worker_context, Mapping)
        and RESERVED_KNOWLEDGE_INDEX_CONTEXT_KEY in worker_context
    ):
        return (
            "worker_execution_context."
            + RESERVED_KNOWLEDGE_INDEX_CONTEXT_KEY
        )
    return None


def has_bound_knowledge_index_job(task: Any) -> bool:
    """Identify the exact persisted v2 control-plane binding."""

    worker_context = _payload(task).get("worker_execution_context")
    job = (
        worker_context.get(RESERVED_KNOWLEDGE_INDEX_CONTEXT_KEY)
        if isinstance(worker_context, Mapping)
        else None
    )
    return bool(
        isinstance(job, Mapping)
        and job.get("schema") == BOUND_KNOWLEDGE_INDEX_JOB_SCHEMA
    )


def reserved_knowledge_index_ingress_error(
    marker: str,
) -> dict[str, Any]:
    return {
        "error": RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
        "code": 403,
        "data": {
            "reason_code": RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON,
            "reserved_field": marker,
        },
    }


def bound_knowledge_index_mutation_error(
    task: Any,
    *,
    action: str,
) -> dict[str, Any] | None:
    if not has_bound_knowledge_index_job(task):
        return None
    task_id = str(_payload(task).get("id") or "").strip()
    return {
        "error": BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
        "code": 409,
        "data": {
            "reason_code": BOUND_KNOWLEDGE_INDEX_MUTATION_REASON,
            "task_id": task_id,
            "action": str(action or "").strip(),
        },
    }


def ensure_generic_knowledge_index_mutation_allowed(
    task: Any,
    *,
    action: str,
) -> None:
    conflict = bound_knowledge_index_mutation_error(
        task,
        action=action,
    )
    if conflict is None:
        return
    data = dict(conflict["data"])
    raise KnowledgeIndexTaskMutationConflict(
        reason_code=str(data["reason_code"]),
        task_id=str(data["task_id"]),
        action=str(data["action"]),
    )


__all__ = [
    "BOUND_KNOWLEDGE_INDEX_JOB_SCHEMA",
    "BOUND_KNOWLEDGE_INDEX_MUTATION_REASON",
    "KnowledgeIndexTaskMutationConflict",
    "RESERVED_KNOWLEDGE_INDEX_CONTEXT_KEY",
    "RESERVED_KNOWLEDGE_INDEX_TASK_INGRESS_REASON",
    "RESERVED_KNOWLEDGE_INDEX_TASK_KIND",
    "RESERVED_KNOWLEDGE_INDEX_TASK_SOURCE",
    "bound_knowledge_index_mutation_error",
    "ensure_generic_knowledge_index_mutation_allowed",
    "find_reserved_knowledge_index_marker",
    "has_bound_knowledge_index_job",
    "reserved_knowledge_index_ingress_error",
]
