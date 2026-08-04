"""Bounded Hub-to-Worker dispatch marker for governed index execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

KNOWLEDGE_INDEX_DISPATCH_SCHEMA = "ananta.knowledge_index_dispatch.v1"
KNOWLEDGE_INDEX_TASK_KIND = "codecompass_index_build"
KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON = (
    "knowledge_index_worker_dispatch_result_pending"
)
KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE = (
    "KnowledgeIndexWorkerDispatchResultPendingError"
)
KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS = 409
SOURCE_ACCESS_MANIFEST_FIELD = "source_access_enforcement_manifest"
MAX_KNOWLEDGE_INDEX_DISPATCH_BYTES = 32 * 1024

_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_PHASES = frozenset({"propose", "execute"})
_BASE_FIELDS = frozenset({"schema", "job_id", "task_kind", "phase"})


class KnowledgeIndexDispatchContractError(ValueError):
    """Raised when a dispatch marker is not the exact bounded contract."""


@dataclass(frozen=True, slots=True)
class KnowledgeIndexDispatch:
    job_id: str
    phase: str
    source_access_manifest: Mapping[str, Any] | None
    marker_digest: str


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_invalid") from exc
    if len(encoded) > MAX_KNOWLEDGE_INDEX_DISPATCH_BYTES:
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_too_large")
    return encoded


def parse_knowledge_index_dispatch(
    value: Mapping[str, Any] | None,
    *,
    expected_phase: str,
    expected_job_id: str,
) -> KnowledgeIndexDispatch:
    """Validate an exact marker and bind it to the request path and job."""

    if not isinstance(value, Mapping):
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_missing")
    marker = dict(value)
    phase = str(marker.get("phase") or "").strip().lower()
    normalized_expected_phase = str(expected_phase or "").strip().lower()
    job_id = str(marker.get("job_id") or "").strip()
    normalized_expected_job_id = str(expected_job_id or "").strip()
    expected_fields = set(_BASE_FIELDS)
    if phase == "execute":
        expected_fields.add(SOURCE_ACCESS_MANIFEST_FIELD)
    if (
        set(marker) != expected_fields
        or marker.get("schema") != KNOWLEDGE_INDEX_DISPATCH_SCHEMA
        or marker.get("task_kind") != KNOWLEDGE_INDEX_TASK_KIND
        or phase not in _PHASES
    ):
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_invalid")
    if phase != normalized_expected_phase:
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_phase_mismatch")
    if _JOB_ID.fullmatch(job_id) is None or job_id != normalized_expected_job_id:
        raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_job_mismatch")
    manifest: Mapping[str, Any] | None = None
    if phase == "execute":
        raw_manifest = marker.get(SOURCE_ACCESS_MANIFEST_FIELD)
        if not isinstance(raw_manifest, Mapping):
            raise KnowledgeIndexDispatchContractError("knowledge_index_dispatch_manifest_invalid")
        normalized_manifest = dict(raw_manifest)
        manifest = MappingProxyType(normalized_manifest)
    encoded = _canonical_bytes(marker)
    return KnowledgeIndexDispatch(
        job_id=job_id,
        phase=phase,
        source_access_manifest=manifest,
        marker_digest=hashlib.sha256(encoded).hexdigest(),
    )


def build_knowledge_index_dispatch(
    *,
    job_id: str,
    phase: str,
    source_access_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only capability-bearing payload accepted by the Worker."""

    marker: dict[str, Any] = {
        "schema": KNOWLEDGE_INDEX_DISPATCH_SCHEMA,
        "job_id": str(job_id or "").strip(),
        "task_kind": KNOWLEDGE_INDEX_TASK_KIND,
        "phase": str(phase or "").strip().lower(),
    }
    if source_access_manifest is not None:
        marker[SOURCE_ACCESS_MANIFEST_FIELD] = dict(source_access_manifest)
    parse_knowledge_index_dispatch(
        marker,
        expected_phase=marker["phase"],
        expected_job_id=marker["job_id"],
    )
    return marker


__all__ = [
    "KNOWLEDGE_INDEX_DISPATCH_SCHEMA",
    "KNOWLEDGE_INDEX_TASK_KIND",
    "KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_ERROR_TYPE",
    "KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_HTTP_STATUS",
    "KNOWLEDGE_INDEX_WORKER_DISPATCH_RESULT_PENDING_REASON",
    "MAX_KNOWLEDGE_INDEX_DISPATCH_BYTES",
    "SOURCE_ACCESS_MANIFEST_FIELD",
    "KnowledgeIndexDispatch",
    "KnowledgeIndexDispatchContractError",
    "build_knowledge_index_dispatch",
    "parse_knowledge_index_dispatch",
]
