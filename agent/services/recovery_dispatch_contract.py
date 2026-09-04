"""Pure values and canonical digests for Hub-owned recovery dispatch."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

_RESULT_CANDIDATE_SCHEMA = "ananta.recovery_result_candidate.v1"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _value(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def recovery_dispatch_request_fingerprint(phase: str, payload: Any) -> str:
    """Canonical digest of caller-controlled scoped execution input."""

    serializer = getattr(payload, "model_dump", None)
    if callable(serializer):
        try:
            raw = serializer(mode="json")
        except TypeError:
            raw = serializer()
    else:
        raw = payload
    data = dict(raw) if isinstance(raw, Mapping) else {}
    normalized_phase = str(phase or "").strip().lower()
    proposal_context_bound = bool(normalized_phase == "execute" and data.get("recovery_proposal_context") is not None)
    run_evidence_context_bound = bool(data.get("recovery_run_evidence_context") is not None)
    if normalized_phase == "propose":
        fields = (
            "prompt",
            "provider",
            "providers",
            "model",
            "temperature",
            "strategy_mode",
            "task_id",
            "research_context",
        )
        if run_evidence_context_bound:
            fields = (*fields, "recovery_run_evidence_context")
    else:
        fields = (
            "command",
            "tool_calls",
            "timeout",
            "task_id",
            "task_kind",
            "retries",
            "retry_delay",
            "retry_policy_override",
        )
        if proposal_context_bound:
            fields = (*fields, "recovery_proposal_context")
        if run_evidence_context_bound:
            fields = (*fields, "recovery_run_evidence_context")
    defaults = {"timeout": 60, "retries": 0, "retry_delay": 1}
    canonical = {
        "schema": (
            "ananta.recovery_dispatch_request.v2"
            if proposal_context_bound or run_evidence_context_bound
            else "ananta.recovery_dispatch_request.v1"
        ),
        "phase": normalized_phase,
        "payload": {field: data.get(field, defaults.get(field)) for field in fields},
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recovery_accepted_result_digest(task: Any) -> str:
    """Bind a Hub-accepted Task result to its authoritative persisted fields."""

    output = str(_value(task, "last_output") or "")
    payload = {
        "schema": "ananta.recovery_accepted_result.v1",
        "task_id": str(_value(task, "id") or ""),
        "status": str(_value(task, "status") or "").strip().lower(),
        "last_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "last_output_length": len(output),
        "last_exit_code": _value(task, "last_exit_code"),
        "verification_status": _mapping(_value(task, "verification_status")),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_recovery_result_candidate(
    *,
    task_id: str,
    status: str,
    verification_record_id: str,
    lease_revision: int,
    lease_token_digest: str,
    request_fingerprint: str,
) -> dict[str, Any]:
    """Build the Hub-only handoff published atomically by result admission."""

    normalized_status = str(status or "").strip().lower()
    normalized_task_id = str(task_id or "").strip()
    normalized_record_id = str(verification_record_id or "").strip()
    normalized_token_digest = str(lease_token_digest or "").strip()
    normalized_fingerprint = str(request_fingerprint or "").strip()
    try:
        normalized_revision = int(lease_revision)
    except (TypeError, ValueError) as exc:
        raise ValueError("recovery_result_candidate_invalid") from exc
    if (
        not normalized_task_id
        or not normalized_record_id
        or normalized_revision < 1
        or len(normalized_token_digest) != 64
        or len(normalized_fingerprint) != 64
        or normalized_status not in {"completed", "verification_failed"}
    ):
        raise ValueError("recovery_result_candidate_invalid")
    return {
        "schema": _RESULT_CANDIDATE_SCHEMA,
        "task_id": normalized_task_id,
        "phase": "execute",
        "status": normalized_status,
        "verification_record_id": normalized_record_id,
        "lease_revision": normalized_revision,
        "lease_token_digest": normalized_token_digest,
        "request_fingerprint": normalized_fingerprint,
        "state": "staged",
        "staged_at": time.time(),
    }


def task_copy(task: Any) -> Any:
    """Return a detached aggregate so a failed save cannot leak mutations."""

    model_dump = getattr(task, "model_dump", None)
    model_validate = getattr(type(task), "model_validate", None)
    if callable(model_dump) and callable(model_validate):
        try:
            return model_validate(model_dump())
        except (TypeError, ValueError):
            pass
    model_copy = getattr(task, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    return copy.deepcopy(task)


@dataclass(frozen=True)
class RecoveryDispatchGateDecision:
    allowed: bool
    reason_code: str
    source_task_id: str | None = None
    plan_id: str | None = None
    release_epoch: str | None = None


@dataclass(frozen=True)
class RecoveryDispatchLease:
    """Opaque, persisted permit for one recovery Worker transport."""

    decision: RecoveryDispatchGateDecision
    phase: str
    token: str | None = None
    expires_at: float | None = None

    @property
    def allowed(self) -> bool:
        return bool(self.decision.allowed)


__all__ = [
    "RecoveryDispatchGateDecision",
    "RecoveryDispatchLease",
    "build_recovery_result_candidate",
    "recovery_accepted_result_digest",
    "recovery_dispatch_request_fingerprint",
    "task_copy",
]
