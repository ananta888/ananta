"""Hub <-> Worker contract for delegated CodeCompass ``chunks`` layer builds.

The Hub queues a task of ``TASK_KIND`` whose ``worker_execution_context``
carries only the small ``JobTicket``. The Worker reads the job, pulls the
redacted file contents in bounded parts and uploads the built layer over the
internal endpoints below (registered-worker auth, ``WORKER_SCOPE``); its
task result echoes the ticket so the Hub admits it only for that exact
dispatch.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

TASK_KIND = "codecompass_layer_build"
CONTEXT_KEY = "codecompass_layer_job"
TICKET_SCHEMA = "ananta.codecompass_layer_ticket.v1"
JOB_SPEC_SCHEMA = "ananta.codecompass_layer_job_spec.v1"
CONTENT_PART_SCHEMA = "ananta.codecompass_layer_content_part.v1"
RESULT_SCHEMA = "ananta.codecompass_layer_job_result.v1"
WORKER_CAPABILITY = "codecompass_layer_build"
REQUIRED_CAPABILITIES = ("retrieval", "index_write", WORKER_CAPABILITY)
WORKER_SCOPE = "codecompass.layers.jobs"
LAYER_MEDIA_TYPE = "application/vnd.ananta.codecompass-layer+gzip"

JOB_PATH = "/internal/codecompass/layer-jobs/{task_id}"
CONTENT_PATH = "/internal/codecompass/layer-jobs/{task_id}/content"
LAYER_PATH = "/internal/codecompass/layer-jobs/{task_id}/layer"

CONTENT_PART_MAX_CHARS = 24 * 1024 * 1024
LAYER_UPLOAD_MAX_BYTES = 512 * 1024 * 1024

_TASK_ID = re.compile(r"cc_layer_[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def canonical_digest(value: Any) -> str:
    """Same canonical form as the Hub's layer dispatch backend (ASCII JSON, sorted keys)."""
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def valid_task_id(value: Any) -> str:
    text = str(value or "")
    if not _TASK_ID.fullmatch(text):
        raise ValueError("codecompass_layer_task_id_invalid")
    return text


@dataclass(frozen=True)
class JobTicket:
    """What the task carries: the binding the Worker must echo, nothing to build from."""

    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    intent_digest: str
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema": TICKET_SCHEMA, **asdict(self)}

    @classmethod
    def from_mapping(cls, value: Any) -> "JobTicket":
        if not isinstance(value, Mapping) or value.get("schema") != TICKET_SCHEMA:
            raise ValueError("codecompass_layer_ticket_invalid")
        ticket = cls(
            task_id=valid_task_id(value.get("task_id")),
            assignment_id=str(value.get("assignment_id") or ""),
            dispatch_lease_id=str(value.get("dispatch_lease_id") or ""),
            intent_digest=str(value.get("intent_digest") or ""),
            run_id=str(value.get("run_id") or ""),
        )
        if not ticket.assignment_id or not ticket.dispatch_lease_id or not _SHA256.fullmatch(ticket.intent_digest):
            raise ValueError("codecompass_layer_ticket_invalid")
        return ticket


def partition_by_size(items: list[tuple[str, int]], max_chars: int = CONTENT_PART_MAX_CHARS) -> list[list[str]]:
    """Deterministic parts of ``(key, size)`` items, each at most ``max_chars`` (one oversized item alone)."""
    parts: list[list[str]] = []
    current: list[str] = []
    used = 0
    for key, size in sorted(items):
        if current and used + size > max_chars:
            parts.append(current)
            current, used = [], 0
        current.append(key)
        used += size
    if current:
        parts.append(current)
    return parts
