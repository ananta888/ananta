"""Canonical contract primitives of Hub Recovery run evidence.

Split out of ``recovery_hub_run_evidence_service`` (SRP): record/catalog/
result-binding schema identifiers and field sets, canonical JSON digests and
the dispatch-lease binding projection.  The service module re-exports the
public names, so existing imports keep working.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any


RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA = (
    "ananta.recovery_hub_tool_run_record.v1"
)
RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA = (
    "ananta.recovery_hub_run_evidence_catalog.v1"
)
_RESULT_BINDING_SCHEMA = (
    "ananta.recovery_hub_tool_run_result_binding.v1"
)
_RUN_SOURCE_ID = "RUN_0001"
_RECORD_FIELDS = frozenset(
    {
        "schema",
        "record_id",
        "source_id",
        "source_type",
        "task_id",
        "state",
        "reserved_at",
        "allowed_for_llm_scope",
        "worker_url",
        "proposal_lease",
        "execute_lease",
        "result_binding",
        "evidence_entry",
        "record_digest",
    }
)
_CATALOG_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "record_id",
        "result_binding",
        "entries",
        "catalog_digest",
    }
)
_LEASE_BINDING_FIELDS = frozenset(
    {
        "revision",
        "token_digest",
        "request_fingerprint",
        "worker_url",
    }
)
_RESULT_BINDING_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "record_id",
        "source_id",
        "lease_revision",
        "lease_token_digest",
        "request_fingerprint",
        "worker_url",
        "worker_result_digest",
        "command_sha256",
        "command_length",
        "output_sha256",
        "output_length",
        "exit_code",
        "response_status",
        "digest",
    }
)


class RecoveryHubRunEvidenceError(ValueError):
    """Raised when Hub run-evidence state is malformed or replayed."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


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
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_evidence_not_json"
        ) from exc


def _digest(value: Mapping[str, Any], *, field: str) -> str:
    payload = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != field
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _lease_binding(lease: Mapping[str, Any]) -> dict[str, Any]:
    try:
        revision = int(lease.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_lease_binding_invalid"
        ) from exc
    binding = {
        "revision": revision,
        "token_digest": str(
            lease.get("token_digest") or ""
        ),
        "request_fingerprint": str(
            lease.get("request_fingerprint") or ""
        ),
        "worker_url": str(
            lease.get("worker_url") or ""
        ).strip().rstrip("/"),
    }
    if (
        revision < 1
        or len(binding["token_digest"]) != 64
        or len(binding["request_fingerprint"]) != 64
        or not binding["worker_url"]
    ):
        raise RecoveryHubRunEvidenceError(
            "recovery_hub_run_lease_binding_invalid"
        )
    return binding


def _latest_execution_event(task: Any) -> dict[str, Any]:
    for value in reversed(list(_value(task, "history") or [])):
        if (
            isinstance(value, Mapping)
            and str(value.get("event_type") or "")
            == "execution_result"
        ):
            return dict(value)
    return {}


__all__ = [
    "RECOVERY_HUB_RUN_EVIDENCE_CATALOG_SCHEMA",
    "RECOVERY_HUB_TOOL_RUN_RECORD_SCHEMA",
    "RecoveryHubRunEvidenceError",
]
