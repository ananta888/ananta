"""Pure validation policy for Hub-owned recovery task mutations."""

from __future__ import annotations

import time
from typing import Any

_TERMINAL_TASK_STATUSES = frozenset(
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
_LEASE_SAME_REVISION_TRANSITIONS = {
    "active": frozenset({"active", "worker_admitted"}),
    "worker_admitted": frozenset({"worker_admitted", "result_accepted"}),
    "result_accepted": frozenset({"result_accepted"}),
    "revoked": frozenset({"revoked"}),
    "cancelled": frozenset({"cancelled"}),
}
_LEASE_NEXT_REVISION_STATES = frozenset({"active", "revoked", "cancelled"})
_LEASE_STABLE_BINDING_FIELDS = (
    "schema",
    "task_id",
    "source_task_id",
    "goal_id",
    "plan_id",
    "team_id",
    "release_epoch",
)
_LEASE_CAPABILITY_BINDING_FIELDS = (
    "token_digest",
    "phase",
    "worker_url",
    "request_fingerprint",
    "issued_at",
    "expires_at",
)
_LEASE_ADMISSION_FIELDS = (
    "admitted_at",
    "admitted_worker_url",
)
_LEASE_ACCEPTANCE_FIELDS = (
    "accepted_at",
    "accepted_result_phase",
    "accepted_result_status",
    "accepted_result_terminal",
    "accepted_result_digest",
)
_LEASE_REVOCATION_FIELDS = (
    "revoked_at",
    "revocation_reason",
)
_LEASE_CANCELLATION_FIELDS = (
    "cancelled_at",
    "cancellation_reason",
)
_LEASE_ARTIFACT_MANIFEST_BINDING = "artifact_manifest_binding"
_RECOVERY_STATE_ORDER = {
    "pending_approval": 10,
    "materialized": 20,
    "materialized_waiting_for_children": 20,
    "denied": 30,
    "expired": 30,
    "superseded": 30,
    "approval_missing": 30,
    "stopped": 30,
    "failed": 30,
    "cancelled": 30,
    "completed": 30,
}
_RECOVERY_BINDING_FIELDS = (
    "source_task_id",
    "goal_id",
    "plan_id",
    "team_id",
    "derivation_reason",
    "derivation_depth",
)
_RECOVERY_EXECUTION_FIELDS = (
    "title",
    "description",
    "priority",
    "parent_task_id",
    "plan_node_id",
    "task_kind",
    "retrieval_intent",
    "required_context_scope",
    "preferred_bundle_mode",
    "required_capabilities",
    "context_bundle_id",
    "worker_execution_context",
    "worker_execution_contract",
    "expected_artifacts",
    "verification_spec",
    "depends_on",
)
_RECOVERY_SOURCE_RESULT_SCHEMA = "ananta.recovery_source_result.v2"
_RECOVERY_SOURCE_POST_COMMIT_SCHEMA = "ananta.recovery_source_post_commit.v1"
_RECOVERY_OWNER_TERMINAL_SCHEMA = "ananta.recovery_owner_terminal_invalidation.v1"
_RECOVERY_OWNER_TERMINAL_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "goal_id",
        "goal_status",
        "previous_status",
        "target_status",
        "reason_code",
        "invalidated_at",
    }
)
_RECOVERY_POST_COMMIT_BINDING_FIELDS = (
    "schema",
    "transition_status",
    "transition_reason",
    "transition_id",
    "old_status",
    "created_at",
)
_RECOVERY_POST_COMMIT_FIELDS = frozenset(
    {
        *_RECOVERY_POST_COMMIT_BINDING_FIELDS,
        "state",
        "processing_at",
        "attempt_id",
        "attempt_count",
        "last_error",
        "failed_at",
        "completed_at",
    }
)


def _details(value: Any) -> dict[str, Any]:
    return dict(getattr(value, "status_reason_details", None) or {}) if value is not None else {}


def _is_recovery_task(value: Any) -> bool:
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )

    return recovery_task_role(value) is not None


def _is_recovery_child(value: Any) -> bool:
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )

    return recovery_task_role(value) == "child"


def _is_recovery_source(value: Any) -> bool:
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )

    return recovery_task_role(value) == "source"


def _is_initial_terminal_transition(
    authoritative: Any,
    candidate: Any,
) -> bool:
    return bool(
        str(getattr(authoritative, "status", "") or "").strip().lower() not in _TERMINAL_TASK_STATUSES
        and str(getattr(candidate, "status", "") or "").strip().lower() in _TERMINAL_TASK_STATUSES
    )


def _recovery_binding_mismatches(
    authoritative: Any,
    candidate: Any,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    for field in _RECOVERY_BINDING_FIELDS:
        current = getattr(authoritative, field, None)
        proposed = getattr(candidate, field, None)
        if field == "derivation_depth":
            try:
                current_value = int(current or 0)
                proposed_value = int(proposed or 0)
            except (TypeError, ValueError):
                mismatches.append(field)
                continue
        else:
            current_value = str(current or "").strip()
            proposed_value = str(proposed or "").strip()
        if current_value != proposed_value:
            mismatches.append(field)
    return tuple(mismatches)


def _recovery_execution_mismatches(
    authoritative: Any,
    candidate: Any,
) -> tuple[str, ...]:
    return tuple(
        field
        for field in _RECOVERY_EXECUTION_FIELDS
        if getattr(authoritative, field, None) != getattr(candidate, field, None)
    )


def _lease_revision(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        revision = int(value or 0)
    except (TypeError, ValueError):
        return None
    if revision < 0 or revision > 2_147_483_647:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return revision


def _lease_state(value: dict[str, Any]) -> str:
    state = str(value.get("state") or "").strip().lower()
    if state in _LEASE_SAME_REVISION_TRANSITIONS:
        return state
    return ""


def _lease_field_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return str(left or "").strip() == str(right or "").strip()
    return left == right


def _lease_fields_match(
    current: dict[str, Any],
    proposed: dict[str, Any],
    fields: tuple[str, ...],
) -> bool:
    return all(
        key not in current or (key in proposed and _lease_field_equal(current[key], proposed[key])) for key in fields
    )


def _lease_is_expired(value: dict[str, Any]) -> bool:
    try:
        expires_at = float(value.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return True
    return expires_at <= time.time()


def _sha256_hex(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(len(normalized) == 64 and all(character in "0123456789abcdef" for character in normalized))


def _source_finalization_publication_valid(
    task: Any,
    *,
    expected_old_status: str | None = None,
) -> bool:
    """Recognize the closed aggregate written by the Hub finalizer."""

    status = str(getattr(task, "status", "") or "").strip().lower()
    expected_result_status = {
        "completed": "passed",
        "verification_failed": "failed",
    }.get(status)
    details = _details(task)
    marker = details.get("recovery_source_post_commit")
    verification = dict(getattr(task, "verification_status", None) or {})
    result = verification.get("model_recovery_result")
    if not isinstance(marker, dict) or not isinstance(result, dict):
        return False
    reason_code = str(getattr(task, "status_reason_code", "") or "").strip()
    raw_artifacts = result.get("artifacts")
    artifact_count = result.get("artifact_count")
    return bool(
        not _is_recovery_child(task)
        and expected_result_status is not None
        and set(marker).issubset(_RECOVERY_POST_COMMIT_FIELDS)
        and marker.get("schema") == _RECOVERY_SOURCE_POST_COMMIT_SCHEMA
        and marker.get("state") in {"pending", "processing", "completed"}
        and marker.get("transition_status") == status
        and marker.get("transition_reason") == reason_code
        and _sha256_hex(marker.get("transition_id"))
        and (
            expected_old_status is None
            or str(marker.get("old_status") or "").strip().lower() == str(expected_old_status or "").strip().lower()
        )
        and result.get("schema") == _RECOVERY_SOURCE_RESULT_SCHEMA
        and result.get("status") == expected_result_status
        and str(result.get("reason_code") or "").strip() == reason_code
        and isinstance(raw_artifacts, list)
        and isinstance(artifact_count, int)
        and not isinstance(artifact_count, bool)
        and artifact_count == len(raw_artifacts)
    )


def _initial_source_finalization_candidate(
    authoritative: Any,
    candidate: Any,
) -> bool:
    authoritative_status = str(getattr(authoritative, "status", "") or "").strip().lower()
    candidate_marker = _details(candidate).get("recovery_source_post_commit")
    return bool(
        authoritative_status not in _TERMINAL_TASK_STATUSES
        and isinstance(candidate_marker, dict)
        and candidate_marker.get("state") == "pending"
        and _source_finalization_publication_valid(
            candidate,
            expected_old_status=authoritative_status,
        )
    )


def _initial_source_finalization_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not _initial_source_finalization_candidate(
        authoritative,
        candidate,
    ):
        return False
    from agent.common.recovery_source_finalization_write_boundary import (
        recovery_source_finalization_write_authorized,
    )

    return recovery_source_finalization_write_authorized(str(getattr(candidate, "id", "") or ""))


def _source_post_commit_progression_candidate(
    authoritative: Any,
    candidate: Any,
) -> bool:
    current = _details(authoritative).get("recovery_source_post_commit")
    proposed = _details(candidate).get("recovery_source_post_commit")
    return bool(isinstance(current, dict) and isinstance(proposed, dict) and current != proposed)


def _source_post_commit_progression_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not _source_post_commit_progression_candidate(
        authoritative,
        candidate,
    ):
        return False
    from agent.common.recovery_source_post_commit_write_boundary import (
        recovery_source_post_commit_write_authorized,
    )

    current = _details(authoritative)["recovery_source_post_commit"]
    proposed = _details(candidate)["recovery_source_post_commit"]
    return recovery_source_post_commit_write_authorized(
        task_id=str(getattr(candidate, "id", "") or ""),
        current=current,
        proposed=proposed,
    )


def _initial_execute_result_acceptance_candidate(
    authoritative: Any,
    candidate: Any,
) -> bool:
    current = _details(authoritative).get("recovery_dispatch_lease")
    proposed = _details(candidate).get("recovery_dispatch_lease")
    if not isinstance(current, dict) or not isinstance(
        proposed,
        dict,
    ):
        return False
    return bool(
        _lease_state(current) == "worker_admitted"
        and str(current.get("phase") or "").strip() == "execute"
        and _lease_state(proposed) == "result_accepted"
    )


def _initial_execute_result_acceptance_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not _initial_execute_result_acceptance_candidate(
        authoritative,
        candidate,
    ):
        return False
    from agent.common.recovery_result_commit_write_boundary import (
        recovery_result_commit_write_authorized,
    )

    proposed_lease = _details(candidate).get("recovery_dispatch_lease")
    if (
        not isinstance(proposed_lease, dict)
        or str(proposed_lease.get("accepted_result_status") or "").strip().lower()
        != str(getattr(candidate, "status", "") or "").strip().lower()
    ):
        return False
    return recovery_result_commit_write_authorized(
        task_id=str(getattr(candidate, "id", "") or ""),
        lease=proposed_lease,
    )


def _accepted_execute_result_digest(task: Any) -> str | None:
    lease = _details(task).get("recovery_dispatch_lease")
    if not isinstance(lease, dict):
        return None
    digest = str(lease.get("accepted_result_digest") or "").strip().lower()
    if not (
        _lease_state(lease) == "result_accepted"
        and str(lease.get("accepted_result_phase") or "").strip() == "execute"
        and lease.get("accepted_result_terminal") is True
        and _sha256_hex(digest)
    ):
        return None
    return digest


def _candidate_matches_accepted_execute_result(
    authoritative: Any,
    candidate: Any,
) -> bool:
    expected_digest = _accepted_execute_result_digest(authoritative)
    if expected_digest is None:
        return True
    from agent.services.recovery_dispatch_gate_service import (
        recovery_accepted_result_digest,
    )

    return recovery_accepted_result_digest(candidate) == expected_digest


def _initial_dispatch_abort_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    current_lease = _details(authoritative).get("recovery_dispatch_lease")
    proposed_lease = _details(candidate).get("recovery_dispatch_lease")
    if not isinstance(current_lease, dict) or not isinstance(
        proposed_lease,
        dict,
    ):
        return False
    from agent.common.recovery_dispatch_abort_write_boundary import (
        recovery_dispatch_abort_write_authorized,
    )

    return recovery_dispatch_abort_write_authorized(
        task_id=str(getattr(candidate, "id", "") or ""),
        current_lease=current_lease,
        proposed_lease=proposed_lease,
        target_status=str(getattr(candidate, "status", "") or ""),
    )


def _owner_terminal_invalidation_valid(
    authoritative: Any,
    candidate: Any,
) -> bool:
    marker = _details(candidate).get("recovery_owner_terminal_invalidation")
    if not isinstance(marker, dict):
        return False
    task_id = str(getattr(candidate, "id", "") or "").strip()
    goal_id = str(getattr(authoritative, "goal_id", "") or "").strip()
    previous_status = str(getattr(authoritative, "status", "") or "").strip().lower()
    target_status = str(getattr(candidate, "status", "") or "").strip().lower()
    goal_status = str(marker.get("goal_status") or "").strip().lower()
    return bool(
        set(marker) == _RECOVERY_OWNER_TERMINAL_FIELDS
        and marker.get("schema") == _RECOVERY_OWNER_TERMINAL_SCHEMA
        and str(marker.get("task_id") or "").strip() == task_id
        and str(marker.get("goal_id") or "").strip() == goal_id
        and str(marker.get("previous_status") or "").strip().lower() == previous_status
        and str(marker.get("target_status") or "").strip().lower() == target_status
        and goal_status
        in {
            "completed",
            "failed",
            "cancelled",
            "aborted",
            "timeout",
            "archived",
        }
        and target_status in _TERMINAL_TASK_STATUSES
        and str(marker.get("reason_code") or "") == f"goal_terminal:{goal_status}"
        and _positive_timestamp(marker.get("invalidated_at"))
    )


def _initial_owner_terminal_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not (
        _is_initial_terminal_transition(authoritative, candidate)
        and _owner_terminal_invalidation_valid(
            authoritative,
            candidate,
        )
    ):
        return False
    from agent.common.recovery_owner_terminal_write_boundary import (
        recovery_owner_terminal_write_authorized,
    )

    marker = _details(candidate)["recovery_owner_terminal_invalidation"]
    return recovery_owner_terminal_write_authorized(
        task_id=str(getattr(candidate, "id", "") or ""),
        marker=marker,
    )


def _initial_dependency_reconciliation_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not (
        _is_recovery_child(authoritative)
        and _is_initial_terminal_transition(
            authoritative,
            candidate,
        )
    ):
        return False
    marker = _details(candidate).get("recovery_dependency_reconciliation")
    if not isinstance(marker, dict):
        return False
    task_id = str(getattr(candidate, "id", "") or "").strip()
    if (
        str(marker.get("task_id") or "").strip() != task_id
        or str(marker.get("source_task_id") or "").strip()
        != str(getattr(authoritative, "source_task_id", "") or "").strip()
        or str(marker.get("previous_status") or "").strip().lower()
        != str(getattr(authoritative, "status", "") or "").strip().lower()
        or str(marker.get("target_status") or "").strip().lower()
        != str(getattr(candidate, "status", "") or "").strip().lower()
        or str(marker.get("reason_code") or "") != str(getattr(candidate, "status_reason_code", "") or "")
    ):
        return False
    from agent.common.recovery_dependency_reconciliation_write_boundary import (
        recovery_dependency_reconciliation_write_authorized,
    )

    return recovery_dependency_reconciliation_write_authorized(
        task_id=task_id,
        marker=marker,
    )


def _initial_child_cancellation_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not (
        _is_recovery_child(authoritative)
        and _is_initial_terminal_transition(
            authoritative,
            candidate,
        )
    ):
        return False
    marker = _details(candidate).get("recovery_child_cancellation")
    if not isinstance(marker, dict):
        return False
    task_id = str(getattr(authoritative, "id", "") or "").strip()
    if (
        str(marker.get("task_id") or "").strip() != task_id
        or str(marker.get("source_task_id") or "").strip()
        != str(getattr(authoritative, "source_task_id", "") or "").strip()
        or str(marker.get("goal_id") or "").strip() != str(getattr(authoritative, "goal_id", "") or "").strip()
        or str(marker.get("plan_id") or "").strip() != str(getattr(authoritative, "plan_id", "") or "").strip()
        or str(marker.get("previous_status") or "").strip().lower()
        != str(getattr(authoritative, "status", "") or "").strip().lower()
        or str(marker.get("target_status") or "").strip().lower()
        != str(getattr(candidate, "status", "") or "").strip().lower()
        or str(marker.get("reason_code") or "").strip()
        != str(getattr(candidate, "status_reason_code", "") or "").strip()
    ):
        return False
    from agent.common.recovery_child_cancellation_write_boundary import (
        recovery_child_cancellation_write_authorized,
    )

    return recovery_child_cancellation_write_authorized(
        task_id=task_id,
        marker=marker,
    )


def _source_approval_rebind_attempt(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not _is_recovery_source(authoritative):
        return False
    authoritative_status = str(getattr(authoritative, "status", "") or "").strip().lower()
    candidate_status = str(getattr(candidate, "status", "") or "").strip().lower()
    current = _details(authoritative).get("model_recovery")
    proposed = _details(candidate).get("model_recovery")
    if not isinstance(current, dict) or not isinstance(
        proposed,
        dict,
    ):
        return False
    current_approval_id = str(current.get("approval_request_id") or "").strip()
    proposed_approval_id = str(proposed.get("approval_request_id") or "").strip()
    return bool(
        authoritative_status in {"waiting_for_review", "needs_review"}
        and candidate_status in {"waiting_for_review", "needs_review"}
        and current_approval_id
        and proposed_approval_id
        and current_approval_id != proposed_approval_id
    )


def _source_approval_rebind_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    if not _source_approval_rebind_attempt(
        authoritative,
        candidate,
    ):
        return False
    current = _details(authoritative)["model_recovery"]
    proposed = _details(candidate)["model_recovery"]
    current_verification = dict(getattr(authoritative, "verification_status", None) or {}).get("model_recovery")
    proposed_verification = dict(getattr(candidate, "verification_status", None) or {}).get("model_recovery")
    if (
        current_verification != current
        or proposed_verification != proposed
        or str(getattr(candidate, "status_reason_code", "") or "").strip() != "model_recovery_plan_pending_approval"
    ):
        return False
    from agent.common.recovery_source_approval_rebind_write_boundary import (
        recovery_source_approval_rebind_write_authorized,
    )

    return recovery_source_approval_rebind_write_authorized(
        task_id=str(getattr(authoritative, "id", "") or ""),
        current_state=current,
        proposed_state=proposed,
    )


def _initial_task_admin_archive_publication(
    authoritative: Any,
    candidate: Any,
) -> bool:
    """Recognize only TaskAdmin's exact source archive terminalization."""

    task_id = str(getattr(authoritative, "id", "") or "").strip()
    from_status = str(getattr(authoritative, "status", "") or "").strip().lower()
    to_status = str(getattr(candidate, "status", "") or "").strip().lower()
    if not (
        _is_recovery_source(authoritative)
        and _is_initial_terminal_transition(
            authoritative,
            candidate,
        )
        and to_status == "cancelled"
        and str(getattr(candidate, "status_reason_code", "") or "").strip() == "task_archived"
    ):
        return False
    from agent.common.recovery_task_admin_write_boundary import (
        recovery_task_admin_write_authorized,
    )

    return recovery_task_admin_write_authorized(
        task_id=task_id,
        source_task_id=task_id,
        goal_id=str(getattr(authoritative, "goal_id", "") or ""),
        action="archive",
        from_status=from_status,
        to_status=to_status,
    )


def _attempt_count(value: dict[str, Any]) -> int | None:
    raw = value.get("attempt_count", 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


def _positive_timestamp(value: Any) -> bool:
    return bool(isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0.0)
