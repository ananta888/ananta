"""Pure Recovery dispatch-gate predicates and lease-binding rules.

These functions carry no repository, lock or transport dependency; the
``RecoveryDispatchGateService`` and its collaborators compose them.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Mapping

from agent.common.recovery_dispatch_contract import (
    _RESULT_CANDIDATE_SCHEMA,
    RecoveryDispatchGateDecision,
    _mapping,
    _value,
    recovery_accepted_result_digest,
)

TERMINAL_GOAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "aborted",
    "timeout",
    "archived",
}
TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "verification_failed",
    "skipped",
    "aborted",
    "timeout",
    "archived",
}
DISPATCHABLE_RECOVERY_STATUSES = {
    "todo",
    "created",
    "assigned",
    "proposing",
    "in_progress",
    "delegated",
    "updated",
}
SUCCESSFUL_DEPENDENCY_STATUSES = {"completed"}
RECOVERY_DISPATCH_LEASE_SCHEMA = "ananta.recovery_dispatch_lease.v1"
IN_FLIGHT_LEASE_STATES = {"active", "worker_admitted"}


def is_recovery_child(task: Any) -> bool:
    details = _mapping(
        _value(task, "status_reason_details")
    )
    return bool(
        str(
            _value(task, "derivation_reason") or ""
        )
        == "goal_task_recovery"
        or _mapping(details.get("model_recovery_release"))
    )


def is_recovery_source(task: Any) -> bool:
    if task is None or is_recovery_child(task):
        return False
    details = _mapping(_value(task, "status_reason_details"))
    verification = _mapping(_value(task, "verification_status"))
    return bool(
        _mapping(details.get("model_recovery"))
        or _mapping(details.get("model_recovery_strategy"))
        or _mapping(verification.get("model_recovery"))
        or _mapping(
            verification.get("model_recovery_strategy")
        )
    )


def accepted_terminal_result_is_proven(
    task: Any,
    lease: Mapping[str, Any],
) -> bool:
    """Accept a terminal race winner only with its complete Hub proof."""

    status = str(_value(task, "status") or "").strip().lower()
    expected_digest = str(
        lease.get("accepted_result_digest") or ""
    )
    return bool(
        status in TERMINAL_TASK_STATUSES
        and str(lease.get("state") or "") == "result_accepted"
        and lease.get("accepted_result_terminal") is True
        and str(lease.get("accepted_result_phase") or "")
        == "execute"
        and str(lease.get("accepted_result_status") or "")
        == status
        and len(expected_digest) == 64
        and hmac.compare_digest(
            expected_digest,
            recovery_accepted_result_digest(task),
        )
    )


def validated_result_candidate(
    task: Any,
    *,
    phase: str,
) -> str:
    """Return the Hub-derived terminal status staged for atomic publish."""

    details = _mapping(_value(task, "status_reason_details"))
    candidate = _mapping(
        details.get("recovery_result_candidate")
    )
    lease = _mapping(details.get("recovery_dispatch_lease"))
    task_id = str(_value(task, "id") or "")
    status = str(candidate.get("status") or "").strip().lower()
    verification = _mapping(_value(task, "verification_status"))
    verification_results = _mapping(
        verification.get("results")
    )
    try:
        candidate_revision = int(
            candidate.get("lease_revision") or 0
        )
        lease_revision = int(lease.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "recovery_result_candidate_binding_invalid"
        ) from exc
    if (
        str(candidate.get("schema") or "")
        != _RESULT_CANDIDATE_SCHEMA
        or str(candidate.get("task_id") or "") != task_id
        or str(candidate.get("phase") or "") != phase
        or str(candidate.get("state") or "") != "staged"
        or status not in {"completed", "verification_failed"}
        or candidate_revision != lease_revision
        or not hmac.compare_digest(
            str(candidate.get("lease_token_digest") or ""),
            str(lease.get("token_digest") or ""),
        )
        or not hmac.compare_digest(
            str(candidate.get("request_fingerprint") or ""),
            str(lease.get("request_fingerprint") or ""),
        )
        or not str(candidate.get("verification_record_id") or "")
        or str(candidate.get("verification_record_id") or "")
        != str(verification.get("record_id") or "")
    ):
        raise RuntimeError(
            "recovery_result_candidate_binding_invalid"
        )
    verification_passed = bool(
        str(verification.get("status") or "").strip().lower()
        == "passed"
        and verification_results.get("final_passed") is True
    )
    if (status == "completed") != verification_passed:
        raise RuntimeError(
            "recovery_result_candidate_verification_mismatch"
        )
    return status


def normalize_dispatch_phase(phase: str | None) -> str:
    normalized = str(phase or "").strip().lower()
    return normalized if normalized in {
        "propose",
        "execute",
        "delegate",
    } else ""


def dispatch_token_digest(token: str | None) -> str:
    return hashlib.sha256(
        str(token or "").encode("utf-8")
    ).hexdigest()


def evaluate_lease_binding(
    task: Any,
    *,
    token: str | None,
    phase: str,
    decision: RecoveryDispatchGateDecision,
    allowed_states: set[str],
    worker_url: str | None = None,
    request_fingerprint: str | None = None,
) -> RecoveryDispatchGateDecision:
    """Match the persisted lease against the caller's transport binding."""

    lease = _mapping(
        _mapping(
            _value(task, "status_reason_details")
        ).get("recovery_dispatch_lease")
    )
    token_digest = str(lease.get("token_digest") or "")
    if not token or not token_digest or not hmac.compare_digest(
        token_digest,
        dispatch_token_digest(token),
    ):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_lease_mismatch",
            source_task_id=decision.source_task_id,
            plan_id=decision.plan_id,
            release_epoch=decision.release_epoch,
        )
    if (
        str(lease.get("schema") or "")
        != RECOVERY_DISPATCH_LEASE_SCHEMA
        or str(lease.get("state") or "") not in allowed_states
        or str(lease.get("phase") or "") != phase
        or float(lease.get("expires_at") or 0.0) <= time.time()
        or str(lease.get("source_task_id") or "")
        != str(decision.source_task_id or "")
        or str(lease.get("plan_id") or "")
        != str(decision.plan_id or "")
        or str(lease.get("release_epoch") or "")
        != str(decision.release_epoch or "")
        or not request_fingerprint
        or not hmac.compare_digest(
            str(lease.get("request_fingerprint") or ""),
            str(request_fingerprint or ""),
        )
        or (
            worker_url is not None
            and str(lease.get("worker_url") or "").rstrip("/")
            != str(worker_url or "").rstrip("/")
        )
    ):
        return RecoveryDispatchGateDecision(
            False,
            "recovery_dispatch_lease_inactive",
            source_task_id=decision.source_task_id,
            plan_id=decision.plan_id,
            release_epoch=decision.release_epoch,
        )
    return RecoveryDispatchGateDecision(
        True,
        "recovery_dispatch_lease_valid",
        source_task_id=decision.source_task_id,
        plan_id=decision.plan_id,
        release_epoch=decision.release_epoch,
    )


def denied_like(
    decision: RecoveryDispatchGateDecision,
    reason_code: str,
) -> RecoveryDispatchGateDecision:
    """Deny with ``reason_code`` while keeping the decision's owner binding."""

    return RecoveryDispatchGateDecision(
        False,
        reason_code,
        source_task_id=decision.source_task_id,
        plan_id=decision.plan_id,
        release_epoch=decision.release_epoch,
    )
