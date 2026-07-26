"""Neutral contract for Hub-provided Recovery tool-run identifiers.

The Hub reserves citation identifiers before a Worker invocation and exposes
only this least-privilege projection to the Worker.  The projection does not
make Worker output authoritative; it merely tells the Worker which already
persisted identifier it may report back as a candidate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any


RECOVERY_TOOL_RUN_CONTEXT_SCHEMA = (
    "ananta.recovery_tool_run_context.v1"
)
RECOVERY_RUN_SOURCE_ID_PATTERN = re.compile(r"^RUN_[0-9]{4}$")
_CONTEXT_FIELDS = frozenset(
    {"schema", "task_id", "records", "digest"}
)
_RECORD_FIELDS = frozenset(
    {
        "record_id",
        "source_id",
        "source_type",
        "allowed_for_llm_scope",
    }
)


class RecoveryRunEvidenceContractError(ValueError):
    """Raised when a Hub-provided run-evidence context is malformed."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecoveryRunEvidenceContractError(
            "recovery_tool_run_context_not_json"
        ) from exc


def _digest(value: Mapping[str, Any]) -> str:
    payload = {
        key: value[key]
        for key in value
        if key != "digest"
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def build_recovery_tool_run_context(
    *,
    task_id: str,
    records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the closed projection sent from the Hub to one Worker."""

    normalized_records = [
        {
            "record_id": str(value.get("record_id") or "").strip(),
            "source_id": str(value.get("source_id") or "").strip(),
            "source_type": str(
                value.get("source_type") or ""
            ).strip(),
            "allowed_for_llm_scope": (
                value.get("allowed_for_llm_scope")
            ),
        }
        for value in records
        if isinstance(value, Mapping)
    ]
    payload: dict[str, Any] = {
        "schema": RECOVERY_TOOL_RUN_CONTEXT_SCHEMA,
        "task_id": str(task_id or "").strip(),
        "records": normalized_records,
    }
    payload["digest"] = _digest(payload)
    return validate_recovery_tool_run_context(
        payload,
        task_id=task_id,
    )


def validate_recovery_tool_run_context(
    value: Any,
    *,
    task_id: str,
) -> dict[str, Any]:
    """Validate task binding, identifiers, shape, and canonical digest."""

    if not isinstance(value, Mapping):
        raise RecoveryRunEvidenceContractError(
            "recovery_tool_run_context_required"
        )
    raw = dict(value)
    if set(raw) != _CONTEXT_FIELDS:
        raise RecoveryRunEvidenceContractError(
            "recovery_tool_run_context_fields_invalid"
        )
    expected_task_id = str(task_id or "").strip()
    records = raw.get("records")
    if (
        raw.get("schema") != RECOVERY_TOOL_RUN_CONTEXT_SCHEMA
        or not expected_task_id
        or str(raw.get("task_id") or "") != expected_task_id
        or not isinstance(records, list)
        or not records
        or len(records) > 32
    ):
        raise RecoveryRunEvidenceContractError(
            "recovery_tool_run_context_binding_invalid"
        )
    normalized_records: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    record_ids: set[str] = set()
    for value_record in records:
        if not isinstance(value_record, Mapping):
            raise RecoveryRunEvidenceContractError(
                "recovery_tool_run_context_record_invalid"
            )
        record = dict(value_record)
        if set(record) != _RECORD_FIELDS:
            raise RecoveryRunEvidenceContractError(
                "recovery_tool_run_context_record_invalid"
            )
        record_id = str(record.get("record_id") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        if (
            not record_id
            or len(record_id) > 200
            or record_id in record_ids
            or RECOVERY_RUN_SOURCE_ID_PATTERN.fullmatch(source_id)
            is None
            or source_id in source_ids
            or record.get("source_type") != "tool_run"
            or record.get("allowed_for_llm_scope") is not True
        ):
            raise RecoveryRunEvidenceContractError(
                "recovery_tool_run_context_record_invalid"
            )
        normalized_records.append(
            {
                "record_id": record_id,
                "source_id": source_id,
                "source_type": "tool_run",
                "allowed_for_llm_scope": True,
            }
        )
        record_ids.add(record_id)
        source_ids.add(source_id)
    normalized = {
        "schema": RECOVERY_TOOL_RUN_CONTEXT_SCHEMA,
        "task_id": expected_task_id,
        "records": normalized_records,
        "digest": str(raw.get("digest") or ""),
    }
    actual_digest = normalized["digest"]
    expected_digest = _digest(normalized)
    if (
        len(actual_digest) != 64
        or not hmac.compare_digest(actual_digest, expected_digest)
    ):
        raise RecoveryRunEvidenceContractError(
            "recovery_tool_run_context_digest_mismatch"
        )
    return normalized


def recovery_tool_run_context_from_task(
    task: Any,
) -> dict[str, Any] | None:
    """Read and validate the context projection carried by a Task."""

    if isinstance(task, Mapping):
        task_id = str(task.get("id") or "")
        details = task.get("status_reason_details")
    else:
        task_id = str(getattr(task, "id", "") or "")
        details = getattr(task, "status_reason_details", None)
    if not isinstance(details, Mapping):
        return None
    value = details.get("recovery_tool_run_context")
    if value is None:
        return None
    return validate_recovery_tool_run_context(
        value,
        task_id=task_id,
    )


__all__ = [
    "RECOVERY_RUN_SOURCE_ID_PATTERN",
    "RECOVERY_TOOL_RUN_CONTEXT_SCHEMA",
    "RecoveryRunEvidenceContractError",
    "build_recovery_tool_run_context",
    "recovery_tool_run_context_from_task",
    "validate_recovery_tool_run_context",
]
