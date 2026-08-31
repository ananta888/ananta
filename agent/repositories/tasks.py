import copy
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from sqlalchemy import or_
from sqlmodel import Session, delete, select

from agent.db_models import (
    AgentSessionDB,
    ArchivedTaskDB,
    GoalDB,
    PolicySnapshotDB,
    TaskDB,
    ToolCallDB,
)
from agent.repositories.task_auxiliary_repositories import (
    AgentSessionRepositoryMixin,
    ArchivedTaskRepositoryMixin,
    PolicySnapshotRepositoryMixin,
    TaskAuxiliaryRepositoryDependencies,
    ToolCallRepositoryMixin,
)

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


def _merge_recovery_source_post_commit(
    current: dict[str, Any],
    proposed: Any,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Advance only the finalizer's immutable delivery marker."""

    if not isinstance(proposed, dict):
        return copy.deepcopy(current)
    candidate = copy.deepcopy(proposed)
    if not set(candidate).issubset(_RECOVERY_POST_COMMIT_FIELDS) or any(
        candidate.get(field) != current.get(field) for field in _RECOVERY_POST_COMMIT_BINDING_FIELDS
    ):
        return copy.deepcopy(current)
    if candidate == current:
        return candidate
    from agent.common.recovery_source_post_commit_write_boundary import (
        recovery_source_post_commit_write_authorized,
    )

    if not recovery_source_post_commit_write_authorized(
        task_id=str(task_id or ""),
        current=current,
        proposed=candidate,
    ):
        return copy.deepcopy(current)
    current_state = str(current.get("state") or "")
    candidate_state = str(candidate.get("state") or "")
    current_count = _attempt_count(current)
    candidate_count = _attempt_count(candidate)
    if current_count is None or candidate_count is None:
        return copy.deepcopy(current)
    current_attempt = str(current.get("attempt_id") or "")
    candidate_attempt = str(candidate.get("attempt_id") or "")
    if current_state == "completed":
        return copy.deepcopy(current)
    if (
        current_state == "pending"
        and candidate_state == "processing"
        and candidate_count == current_count + 1
        and candidate_attempt
        and len(candidate_attempt.encode("utf-8")) <= 128
        and candidate_attempt != current_attempt
        and _positive_timestamp(candidate.get("processing_at"))
    ):
        return candidate
    if current_state != "processing":
        return copy.deepcopy(current)
    if (
        candidate_state == "processing"
        and candidate_count == current_count + 1
        and candidate_attempt
        and len(candidate_attempt.encode("utf-8")) <= 128
        and candidate_attempt != current_attempt
        and _positive_timestamp(candidate.get("processing_at"))
        and float(candidate["processing_at"]) >= float(current.get("processing_at") or 0.0)
    ):
        return candidate
    same_claim = bool(candidate_count == current_count and candidate_attempt == current_attempt and candidate_attempt)
    if (
        candidate_state == "pending"
        and same_claim
        and _positive_timestamp(candidate.get("failed_at"))
        and isinstance(candidate.get("last_error"), str)
        and len(str(candidate.get("last_error") or "").encode("utf-8")) <= 500
    ):
        return candidate
    if (
        candidate_state == "completed"
        and same_claim
        and _positive_timestamp(candidate.get("completed_at"))
        and candidate.get("last_error") is None
    ):
        return candidate
    return copy.deepcopy(current)


def _lease_has_terminal_acceptance(value: dict[str, Any]) -> bool:
    return bool(
        _lease_state(value) == "result_accepted"
        and str(value.get("accepted_result_phase") or "") == "execute"
        and str(value.get("accepted_result_status") or "") in {"completed", "verification_failed"}
        and value.get("accepted_result_terminal") is True
        and _sha256_hex(value.get("accepted_result_digest"))
    )


def _new_active_lease_is_valid(value: dict[str, Any]) -> bool:
    try:
        issued_at = float(value.get("issued_at") or 0.0)
        expires_at = float(value.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return False
    return bool(
        str(value.get("schema") or "") == "ananta.recovery_dispatch_lease.v1"
        and str(value.get("phase") or "") in {"propose", "execute"}
        and _sha256_hex(value.get("token_digest"))
        and _sha256_hex(value.get("request_fingerprint"))
        and str(value.get("source_task_id") or "").strip()
        and str(value.get("plan_id") or "").strip()
        and str(value.get("release_epoch") or "").strip()
        and issued_at > 0.0
        and expires_at > max(issued_at, time.time())
    )


def _merge_same_revision_lease(
    current: dict[str, Any],
    proposed: dict[str, Any],
    *,
    task_id: str,
    current_state: str,
    proposed_state: str,
) -> dict[str, Any]:
    lifecycle_fields = (
        _LEASE_ADMISSION_FIELDS + _LEASE_ACCEPTANCE_FIELDS + _LEASE_REVOCATION_FIELDS + _LEASE_CANCELLATION_FIELDS
    )
    if not _lease_fields_match(
        current,
        proposed,
        lifecycle_fields,
    ):
        return current
    if current_state == "active" and proposed_state == "worker_admitted":
        try:
            admitted_at = float(proposed.get("admitted_at") or 0.0)
        except (TypeError, ValueError):
            return current
        admitted_worker = str(proposed.get("admitted_worker_url") or "").strip().rstrip("/")
        bound_worker = str(proposed.get("worker_url") or "").strip().rstrip("/")
        if admitted_at <= 0.0 or (bound_worker and admitted_worker != bound_worker):
            return current
    if current_state == "worker_admitted" and proposed_state == "result_accepted":
        accepted_phase = str(proposed.get("accepted_result_phase") or "").strip()
        accepted_status = str(proposed.get("accepted_result_status") or "").strip()
        try:
            accepted_at = float(proposed.get("accepted_at") or 0.0)
        except (TypeError, ValueError):
            return current
        if (
            accepted_at <= 0.0
            or accepted_phase != str(current.get("phase") or "").strip()
            or not accepted_status
            or not isinstance(
                proposed.get("accepted_result_terminal"),
                bool,
            )
        ):
            return current
        if accepted_phase == "execute" and not (
            str(proposed.get("accepted_result_status") or "") in {"completed", "verification_failed"}
            and proposed.get("accepted_result_terminal") is True
            and _sha256_hex(proposed.get("accepted_result_digest"))
        ):
            return current
        if accepted_phase == "execute":
            from agent.common.recovery_result_commit_write_boundary import (
                recovery_result_commit_write_authorized,
            )

            if not recovery_result_commit_write_authorized(
                task_id=task_id,
                lease=proposed,
            ):
                return current

    merged = dict(current)
    for key in _LEASE_STABLE_BINDING_FIELDS + _LEASE_CAPABILITY_BINDING_FIELDS:
        if key not in merged and key in proposed:
            merged[key] = proposed[key]
    merged["state"] = proposed_state
    if proposed_state == "worker_admitted":
        for key in _LEASE_ADMISSION_FIELDS:
            if key in proposed:
                merged[key] = proposed[key]
    elif proposed_state == "result_accepted":
        for key in _LEASE_ACCEPTANCE_FIELDS:
            if key in proposed:
                merged[key] = proposed[key]
    elif proposed_state == "revoked":
        for key in _LEASE_REVOCATION_FIELDS:
            if key in proposed:
                merged[key] = proposed[key]
    elif proposed_state == "cancelled":
        for key in _LEASE_CANCELLATION_FIELDS:
            if key in proposed:
                merged[key] = proposed[key]
    current_manifest_binding = current.get(_LEASE_ARTIFACT_MANIFEST_BINDING)
    proposed_manifest_binding = proposed.get(_LEASE_ARTIFACT_MANIFEST_BINDING)
    if isinstance(current_manifest_binding, dict):
        merged[_LEASE_ARTIFACT_MANIFEST_BINDING] = copy.deepcopy(current_manifest_binding)
    elif isinstance(proposed_manifest_binding, dict):
        from agent.common.recovery_artifact_manifest_write_boundary import (
            recovery_artifact_manifest_write_authorized,
        )

        if recovery_artifact_manifest_write_authorized(
            task_id=task_id,
            lease=current,
            binding=proposed_manifest_binding,
        ):
            merged[_LEASE_ARTIFACT_MANIFEST_BINDING] = copy.deepcopy(proposed_manifest_binding)
    return merged


def _new_active_lease(
    current: dict[str, Any],
    proposed: dict[str, Any],
) -> dict[str, Any]:
    lifecycle_fields = set(
        _LEASE_ADMISSION_FIELDS + _LEASE_ACCEPTANCE_FIELDS + _LEASE_REVOCATION_FIELDS + _LEASE_CANCELLATION_FIELDS
    )
    capability_fields = set(_LEASE_CAPABILITY_BINDING_FIELDS)
    merged = {
        key: value
        for key, value in current.items()
        if key not in lifecycle_fields
        and key not in capability_fields
        and key
        not in {
            "state",
            "revision",
            _LEASE_ARTIFACT_MANIFEST_BINDING,
        }
    }
    for key in _LEASE_STABLE_BINDING_FIELDS + _LEASE_CAPABILITY_BINDING_FIELDS:
        if key in proposed:
            merged[key] = proposed[key]
    for key in _LEASE_STABLE_BINDING_FIELDS:
        if key in current:
            merged[key] = current[key]
    merged["state"] = "active"
    merged["revision"] = proposed["revision"]
    return merged


def _merge_invalidated_lease(
    current: dict[str, Any],
    proposed: dict[str, Any],
    *,
    state: str,
) -> dict[str, Any]:
    merged = dict(current)
    merged["state"] = state
    merged["revision"] = proposed["revision"]
    fields = _LEASE_REVOCATION_FIELDS if state == "revoked" else _LEASE_CANCELLATION_FIELDS
    for key in fields:
        if key in proposed:
            merged[key] = proposed[key]
    return merged


def _merge_dispatch_lease(
    authoritative: Any,
    candidate: Any,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Merge a detached lease without allowing capability escalation.

    A lease revision is a Hub-owned capability epoch.  Within one epoch only
    admission and result acceptance may advance.  The next revision may
    invalidate the current epoch or issue a new active capability, but an
    unexpired in-flight capability cannot be displaced by an ordinary save.
    """

    current = dict(authoritative) if isinstance(authoritative, dict) else {}
    proposed = dict(candidate) if isinstance(candidate, dict) else {}
    expected_task_id = str(task_id or "").strip()
    if expected_task_id and any(
        str(lease.get("task_id") or "").strip() not in {"", expected_task_id} for lease in (current, proposed) if lease
    ):
        return current
    if not current:
        proposed_revision = _lease_revision(proposed.get("revision"))
        if (
            not proposed
            or proposed_revision != 1
            or _lease_state(proposed) != "active"
            or not _new_active_lease_is_valid(proposed)
            or (expected_task_id and str(proposed.get("task_id") or "").strip() != expected_task_id)
        ):
            return {}
        proposed["revision"] = proposed_revision
        return _new_active_lease({}, proposed)
    if not proposed:
        return current

    current_revision = _lease_revision(current.get("revision"))
    proposed_revision = _lease_revision(proposed.get("revision"))
    if current_revision is None or proposed_revision is None:
        return current
    if proposed_revision < current_revision:
        return current
    if proposed_revision > current_revision + 1:
        return current
    proposed["revision"] = proposed_revision

    current_state = _lease_state(current)
    proposed_state = _lease_state(proposed)
    if not current_state or not proposed_state:
        return current
    if not _lease_fields_match(
        current,
        proposed,
        _LEASE_STABLE_BINDING_FIELDS,
    ):
        return current

    if proposed_revision == current_revision:
        if proposed_state not in _LEASE_SAME_REVISION_TRANSITIONS.get(
            current_state,
            frozenset(),
        ):
            return current
        if not _lease_fields_match(
            current,
            proposed,
            _LEASE_CAPABILITY_BINDING_FIELDS,
        ):
            return current
        return _merge_same_revision_lease(
            current,
            proposed,
            task_id=expected_task_id,
            current_state=current_state,
            proposed_state=proposed_state,
        )

    if proposed_state not in _LEASE_NEXT_REVISION_STATES:
        return current
    if _lease_has_terminal_acceptance(current):
        return current
    if proposed_state == "active":
        if current_state in {"active", "worker_admitted"} and not _lease_is_expired(current):
            return current
        if not _new_active_lease_is_valid(proposed):
            return current
        return _new_active_lease(
            current,
            proposed,
        )

    if not _lease_fields_match(
        current,
        proposed,
        _LEASE_CAPABILITY_BINDING_FIELDS,
    ):
        return current
    from agent.common.recovery_dispatch_invalidation_write_boundary import (
        recovery_dispatch_invalidation_write_authorized,
    )

    if not recovery_dispatch_invalidation_write_authorized(
        task_id=expected_task_id,
        current_lease=current,
        proposed_lease=proposed,
    ):
        return current
    return _merge_invalidated_lease(
        current,
        proposed,
        state=proposed_state,
    )


def _merge_recovery_state(
    authoritative: Any,
    candidate: Any,
) -> dict[str, Any]:
    current = dict(authoritative) if isinstance(authoritative, dict) else {}
    proposed = dict(candidate) if isinstance(candidate, dict) else {}
    if not current:
        return proposed
    if not proposed:
        return current
    current_status = str(current.get("status") or "").strip().lower()
    proposed_status = str(proposed.get("status") or "").strip().lower()
    current_rank = _RECOVERY_STATE_ORDER.get(current_status, 0)
    proposed_rank = _RECOVERY_STATE_ORDER.get(proposed_status, 0)
    merged = {**current, **proposed}
    for key in (
        "schema",
        "plan_id",
        "approval_request_id",
        "recovery_key",
        "release_epoch",
        "created_task_ids",
        "team_id",
        "recovery_depth",
        "dependency_binding",
    ):
        if key in current:
            merged[key] = current[key]
    if current_rank > proposed_rank or (
        current_rank >= 30 and proposed_rank == current_rank and proposed_status != current_status
    ):
        for key in (
            "status",
            "reason_code",
            "updated_at",
            "plan_id",
            "approval_request_id",
            "recovery_key",
        ):
            if key in current:
                merged[key] = current[key]
    return merged


def _merge_recovery_details(
    authoritative: Any,
    candidate: Any,
) -> dict[str, Any]:
    current = _details(authoritative)
    proposed = _details(candidate)
    merged = {**current, **proposed}
    source_approval_rebind = _source_approval_rebind_publication(
        authoritative,
        candidate,
    )
    current_post_commit = current.get("recovery_source_post_commit")
    proposed_post_commit = proposed.get("recovery_source_post_commit")
    if isinstance(current_post_commit, dict):
        merged["recovery_source_post_commit"] = _merge_recovery_source_post_commit(
            current_post_commit,
            proposed_post_commit,
            task_id=str(getattr(authoritative, "id", "") or ""),
        )
    elif _initial_source_finalization_publication(
        authoritative,
        candidate,
    ):
        merged["recovery_source_post_commit"] = copy.deepcopy(proposed_post_commit)
    else:
        merged.pop("recovery_source_post_commit", None)
    current_release = current.get("model_recovery_release")
    proposed_release = proposed.get("model_recovery_release")
    # A materialization release binding is immutable once published.  A
    # detached writer may add unrelated details but cannot erase or replace
    # the Hub-owned Goal/Source/Plan/approval epoch.
    if isinstance(current_release, dict) and current_release:
        merged["model_recovery_release"] = dict(current_release)
    elif isinstance(proposed_release, dict):
        merged["model_recovery_release"] = dict(proposed_release)

    current_owner_terminal = current.get("recovery_owner_terminal_invalidation")
    proposed_owner_terminal = proposed.get("recovery_owner_terminal_invalidation")
    if isinstance(current_owner_terminal, dict):
        merged["recovery_owner_terminal_invalidation"] = copy.deepcopy(current_owner_terminal)
    elif _initial_owner_terminal_publication(
        authoritative,
        candidate,
    ):
        merged["recovery_owner_terminal_invalidation"] = copy.deepcopy(proposed_owner_terminal)
    else:
        merged.pop(
            "recovery_owner_terminal_invalidation",
            None,
        )

    current_dependency_reconciliation = current.get("recovery_dependency_reconciliation")
    proposed_dependency_reconciliation = proposed.get("recovery_dependency_reconciliation")
    if isinstance(current_dependency_reconciliation, dict):
        merged["recovery_dependency_reconciliation"] = copy.deepcopy(current_dependency_reconciliation)
    elif _initial_dependency_reconciliation_publication(
        authoritative,
        candidate,
    ):
        merged["recovery_dependency_reconciliation"] = copy.deepcopy(proposed_dependency_reconciliation)
    else:
        merged.pop(
            "recovery_dependency_reconciliation",
            None,
        )

    current_child_cancellation = current.get("recovery_child_cancellation")
    proposed_child_cancellation = proposed.get("recovery_child_cancellation")
    if isinstance(current_child_cancellation, dict):
        merged["recovery_child_cancellation"] = copy.deepcopy(current_child_cancellation)
    elif _initial_child_cancellation_publication(
        authoritative,
        candidate,
    ):
        merged["recovery_child_cancellation"] = copy.deepcopy(proposed_child_cancellation)
    else:
        merged.pop("recovery_child_cancellation", None)

    current_recovery = current.get("model_recovery")
    proposed_recovery = proposed.get("model_recovery")
    if source_approval_rebind:
        merged["model_recovery"] = copy.deepcopy(proposed_recovery)
    elif isinstance(current_recovery, dict):
        merged["model_recovery"] = _merge_recovery_state(
            current_recovery,
            proposed_recovery,
        )

    current_strategy = current.get("model_recovery_strategy")
    proposed_strategy = proposed.get("model_recovery_strategy")
    if isinstance(current_strategy, dict):
        merged["model_recovery_strategy"] = _merge_recovery_state(
            current_strategy,
            proposed_strategy,
        )

    merged["recovery_dispatch_lease"] = _merge_dispatch_lease(
        current.get("recovery_dispatch_lease"),
        proposed.get("recovery_dispatch_lease"),
        task_id=str(getattr(authoritative, "id", "") or ""),
    )
    if not merged["recovery_dispatch_lease"]:
        merged.pop("recovery_dispatch_lease", None)
    return merged


def _merge_recovery_verification(
    authoritative: Any,
    candidate: Any,
) -> dict[str, Any]:
    current = dict(getattr(authoritative, "verification_status", None) or {})
    proposed = dict(getattr(candidate, "verification_status", None) or {})
    merged = {**current, **proposed}
    source_approval_rebind = _source_approval_rebind_publication(
        authoritative,
        candidate,
    )
    for key in ("model_recovery", "model_recovery_strategy"):
        if key == "model_recovery" and source_approval_rebind:
            merged[key] = copy.deepcopy(proposed.get(key))
            continue
        if isinstance(current.get(key), dict):
            merged[key] = _merge_recovery_state(
                current.get(key),
                proposed.get(key),
            )
    if isinstance(current.get("model_recovery_release"), dict):
        merged["model_recovery_release"] = dict(current["model_recovery_release"])
    current_result = current.get("model_recovery_result")
    if isinstance(current_result, dict):
        # The source aggregate is a Hub publication. Once persisted, a
        # detached same-status writer may neither erase nor replace it.
        merged["model_recovery_result"] = copy.deepcopy(current_result)
    elif _initial_source_finalization_publication(
        authoritative,
        candidate,
    ):
        merged["model_recovery_result"] = copy.deepcopy(proposed.get("model_recovery_result"))
    else:
        merged.pop("model_recovery_result", None)
    return merged


def _task_recovery_lifecycle_rank(value: Any) -> int:
    ranks: list[int] = []
    for field in ("status_reason_details", "verification_status"):
        payload = dict(getattr(value, field, None) or {})
        for key in ("model_recovery", "model_recovery_strategy"):
            state = payload.get(key)
            if isinstance(state, dict):
                ranks.append(
                    _RECOVERY_STATE_ORDER.get(
                        str(state.get("status") or "").strip().lower(),
                        0,
                    )
                )
    return max(ranks, default=0)


@dataclass(frozen=True)
class TaskStatusCompareAndSetResult:
    """Outcome of one repository-owned atomic status compare-and-set."""

    updated: bool
    task: TaskDB | None
    previous_status: str | None


_RECOVERY_CHILD_CANCELLATION_CAS_FIELDS = frozenset(
    {
        "status",
        "updated_at",
        "history",
        "status_reason_code",
        "status_reason_details",
    }
)
_RECOVERY_SOURCE_APPROVAL_REBIND_CAS_FIELDS = frozenset(
    {
        "status",
        "updated_at",
        "history",
        "verification_status",
        "status_reason_code",
        "status_reason_details",
    }
)


def _changed_task_fields(
    authoritative: Any,
    candidate: Any,
) -> set[str]:
    return {
        field for field in TaskDB.model_fields if getattr(authoritative, field, None) != getattr(candidate, field, None)
    }


def _detached_task_row_copy(authoritative: TaskDB) -> TaskDB:
    """Copy one ORM row without revalidation or Session instrumentation."""

    candidate = TaskDB(id=str(getattr(authoritative, "id", "") or ""))
    for field in TaskDB.model_fields:
        setattr(
            candidate,
            field,
            copy.deepcopy(getattr(authoritative, field, None)),
        )
    return candidate


def _exact_child_cancellation_history(
    authoritative: Any,
    candidate: Any,
    *,
    marker: dict[str, Any],
) -> bool:
    """Accept only DispatchGate's one bound cancellation history event."""

    proposed_history = list(getattr(candidate, "history", None) or [])
    if not proposed_history:
        return False
    event = proposed_history[-1]
    if not isinstance(event, dict):
        return False
    try:
        timestamp = float(event.get("timestamp"))
        cancelled_at = float(marker.get("cancelled_at"))
        candidate_updated_at = float(getattr(candidate, "updated_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(timestamp) or timestamp < max(cancelled_at, candidate_updated_at) or timestamp > time.time():
        return False

    context = {"task_id": str(getattr(authoritative, "id", "") or "")}
    for field, attribute in (
        ("goal_id", "goal_id"),
        ("trace_id", "goal_trace_id"),
        ("plan_id", "plan_id"),
    ):
        value = getattr(authoritative, attribute, None)
        if value is not None:
            context[field] = value
    expected_event = {
        "version": "v1",
        "kind": "hub_event",
        "channel": "task_history",
        "event_type": "recovery_dispatch_gate_invalidated",
        "timestamp": event.get("timestamp"),
        "actor": "hub_dispatch_gate",
        "context": context,
        "details": {"reason_code": str(marker.get("reason_code") or "")},
    }
    if event != expected_event:
        return False
    current_history = list(getattr(authoritative, "history", None) or [])
    return proposed_history == (current_history + [expected_event])[-200:]


def _exact_source_approval_rebind_history(
    authoritative: Any,
    candidate: Any,
    *,
    proposed_state: dict[str, Any],
) -> bool:
    proposed_history = list(getattr(candidate, "history", None) or [])
    if not proposed_history:
        return False
    event = proposed_history[-1]
    if not isinstance(event, dict):
        return False
    try:
        timestamp = float(event.get("timestamp"))
        candidate_updated_at = float(getattr(candidate, "updated_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(timestamp) or timestamp < candidate_updated_at or timestamp > time.time():
        return False
    context = {"task_id": str(getattr(authoritative, "id", "") or "")}
    for field, attribute in (
        ("goal_id", "goal_id"),
        ("trace_id", "goal_trace_id"),
        ("plan_id", "plan_id"),
    ):
        value = getattr(authoritative, attribute, None)
        if value is not None:
            context[field] = value
    expected_event = {
        "version": "v1",
        "kind": "hub_event",
        "channel": "task_history",
        "event_type": ("task_recovery_plan_pending_approval"),
        "timestamp": event.get("timestamp"),
        "actor": "hub_recovery_planner",
        "context": context,
        "details": {
            "plan_id": str(proposed_state.get("plan_id") or ""),
            "approval_request_id": str(proposed_state.get("approval_request_id") or ""),
        },
    }
    if event != expected_event:
        return False
    current_history = list(getattr(authoritative, "history", None) or [])
    return proposed_history == (current_history + [expected_event])[-200:]


def _recovery_source_approval_rebind_cas_mismatches(
    authoritative: Any,
    candidate: Any,
) -> tuple[str, ...]:
    """Close the approval-rebind capability over its complete row delta."""

    mismatches: list[str] = []
    changed_fields = _changed_task_fields(
        authoritative,
        candidate,
    )
    for field in sorted(changed_fields - _RECOVERY_SOURCE_APPROVAL_REBIND_CAS_FIELDS):
        mismatches.append(field)

    current_state = _details(authoritative).get("model_recovery")
    proposed_state = _details(candidate).get("model_recovery")
    if not isinstance(current_state, dict) or not isinstance(
        proposed_state,
        dict,
    ):
        return tuple(mismatches + ["status_reason_details.model_recovery"])
    expected_details = {
        **_details(authoritative),
        "model_recovery": copy.deepcopy(proposed_state),
    }
    if _details(candidate) != expected_details:
        mismatches.append("status_reason_details")
    current_verification = dict(getattr(authoritative, "verification_status", None) or {})
    expected_verification = {
        **current_verification,
        "model_recovery": copy.deepcopy(proposed_state),
    }
    if dict(getattr(candidate, "verification_status", None) or {}) != expected_verification:
        mismatches.append("verification_status")
    if str(getattr(candidate, "status_reason_code", "") or "").strip() != "model_recovery_plan_pending_approval":
        mismatches.append("status_reason_code")
    try:
        candidate_updated_at = float(getattr(candidate, "updated_at", 0.0) or 0.0)
        authoritative_updated_at = float(getattr(authoritative, "updated_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        mismatches.append("updated_at")
    else:
        if not math.isfinite(candidate_updated_at) or candidate_updated_at < authoritative_updated_at:
            mismatches.append("updated_at")
    if not _exact_source_approval_rebind_history(
        authoritative,
        candidate,
        proposed_state=proposed_state,
    ):
        mismatches.append("history")
    return tuple(dict.fromkeys(mismatches))


def _recovery_child_cancellation_cas_mismatches(
    authoritative: Any,
    candidate: Any,
) -> tuple[str, ...]:
    """Close the exact cancellation capability over its complete row delta."""

    mismatches: list[str] = []
    changed_fields = _changed_task_fields(
        authoritative,
        candidate,
    )
    for field in sorted(changed_fields - _RECOVERY_CHILD_CANCELLATION_CAS_FIELDS):
        mismatches.append(field)

    marker = _details(candidate).get("recovery_child_cancellation")
    if not isinstance(marker, dict):
        return tuple(mismatches + ["status_reason_details.recovery_child_cancellation"])
    current_details = _details(authoritative)
    expected_details = copy.deepcopy(current_details)
    expected_details["recovery_child_cancellation"] = copy.deepcopy(marker)
    if (
        isinstance(
            current_details.get("recovery_child_cancellation"),
            dict,
        )
        or _details(candidate) != expected_details
    ):
        mismatches.append("status_reason_details")
    if str(getattr(candidate, "status_reason_code", "") or "").strip() != str(marker.get("reason_code") or "").strip():
        mismatches.append("status_reason_code")
    try:
        candidate_updated_at = float(getattr(candidate, "updated_at", 0.0) or 0.0)
        authoritative_updated_at = float(getattr(authoritative, "updated_at", 0.0) or 0.0)
        cancelled_at = float(marker.get("cancelled_at"))
    except (TypeError, ValueError):
        mismatches.append("updated_at")
    else:
        if not math.isfinite(candidate_updated_at) or candidate_updated_at < max(
            authoritative_updated_at, cancelled_at
        ):
            mismatches.append("updated_at")
    if not _exact_child_cancellation_history(
        authoritative,
        candidate,
        marker=marker,
    ):
        mismatches.append("history")
    return tuple(dict.fromkeys(mismatches))


def _recovery_status_cas_sensitive_mismatches(
    authoritative: Any,
    candidate: Any,
) -> tuple[str, ...]:
    """Fields that generic status CAS may never publish for Recovery tasks."""

    if not _is_recovery_task(authoritative):
        return ()
    if _source_approval_rebind_attempt(
        authoritative,
        candidate,
    ):
        if _source_approval_rebind_publication(
            authoritative,
            candidate,
        ):
            return _recovery_source_approval_rebind_cas_mismatches(
                authoritative,
                candidate,
            )
        return ("status_reason_details.model_recovery.approval_request_id",)
    if _initial_child_cancellation_publication(
        authoritative,
        candidate,
    ):
        return _recovery_child_cancellation_cas_mismatches(
            authoritative,
            candidate,
        )
    current_details = _details(authoritative)
    proposed_details = _details(candidate)
    mismatches: list[str] = []
    for key in (
        "recovery_dispatch_lease",
        "recovery_result_candidate",
        "recovery_source_post_commit",
        "recovery_hub_tool_run_record",
        "recovery_tool_run_context",
    ):
        if current_details.get(key) != proposed_details.get(key):
            mismatches.append(f"status_reason_details.{key}")
    current_verification = dict(getattr(authoritative, "verification_status", None) or {})
    proposed_verification = dict(getattr(candidate, "verification_status", None) or {})
    if current_verification.get("model_recovery_result") != proposed_verification.get("model_recovery_result"):
        mismatches.append("verification_status.model_recovery_result")
    for field in (
        "last_output",
        "last_exit_code",
        "error",
        "callback_url",
        "callback_token",
        "current_worker_job_id",
    ):
        if getattr(authoritative, field, None) != getattr(
            candidate,
            field,
            None,
        ):
            mismatches.append(field)
    if _is_recovery_child(authoritative):
        if current_verification != proposed_verification:
            mismatches.append("verification_status")
    return tuple(mismatches)


def _apply_organization_workflow_completion_policy(
    authoritative: TaskDB | None,
    candidate: TaskDB,
    *,
    session: Session,
) -> TaskDB:
    """Apply the Hub-owned Organization completion backstop in-transaction."""

    from agent.services.organization_workflow_completion_policy_service import (
        organization_workflow_completion_policy_service,
    )
    from agent.services.organization_workflow_gate_approval_service import (
        organization_workflow_gate_approval_service,
    )

    organization_workflow_gate_approval_service.issue_for_verified_completion(
        authoritative_task=authoritative,
        candidate_task=candidate,
        session=session,
    )

    organization_gate_decision = organization_workflow_completion_policy_service.evaluate(
        authoritative_task=authoritative,
        candidate_task=candidate,
        session=session,
    )
    if (
        authoritative is not None
        and organization_gate_decision.reason_code == "organization_workflow_step_binding_immutable"
    ):
        authoritative_context = dict(authoritative.worker_execution_context or {})
        candidate_context = dict(candidate.worker_execution_context or {})
        candidate_context["organization_workflow_step_binding"] = copy.deepcopy(
            authoritative_context["organization_workflow_step_binding"]
        )
        candidate.worker_execution_context = candidate_context
        organization_gate_decision = organization_workflow_completion_policy_service.evaluate(
            authoritative_task=authoritative,
            candidate_task=candidate,
            session=session,
        )
    if organization_gate_decision.applicable and not organization_gate_decision.allowed:
        organization_workflow_completion_policy_service.pending_status(
            candidate_task=candidate,
            decision=organization_gate_decision,
        )
    return candidate


def _prepare_existing_task_write(
    authoritative: TaskDB,
    candidate: TaskDB,
    *,
    session: Session,
    lock_ids: set[str],
    write_operation: str,
) -> TaskDB | None:
    """Apply the single authoritative Recovery write policy in-transaction."""

    task_id = str(getattr(authoritative, "id", "") or "").strip()
    candidate = _preserve_bound_knowledge_index_context(
        authoritative,
        candidate,
        write_operation=write_operation,
    )
    candidate = _apply_organization_workflow_completion_policy(
        authoritative,
        candidate,
        session=session,
    )
    if write_operation == "status_cas":
        cas_mismatches = _recovery_status_cas_sensitive_mismatches(
            authoritative,
            candidate,
        )
        if cas_mismatches:
            raise ValueError("recovery_status_cas_sensitive_mutation_denied:" + ",".join(cas_mismatches))
    if not (_is_recovery_task(authoritative) or _is_recovery_task(candidate)):
        return candidate

    if _is_recovery_task(authoritative):
        binding_mismatches = _recovery_binding_mismatches(
            authoritative,
            candidate,
        )
        if binding_mismatches:
            logging.warning(
                "Rejected recovery-task binding mutation %s: %s",
                task_id,
                ",".join(binding_mismatches),
            )
            raise ValueError("recovery_task_binding_immutable:" + ",".join(binding_mismatches))
        if _is_recovery_child(authoritative):
            execution_mismatches = _recovery_execution_mismatches(
                authoritative,
                candidate,
            )
            if execution_mismatches:
                logging.warning(
                    "Rejected recovery-child execution payload mutation %s: %s",
                    task_id,
                    ",".join(execution_mismatches),
                )
                raise ValueError("recovery_child_execution_payload_immutable:" + ",".join(execution_mismatches))
        if (
            _is_recovery_source(authoritative)
            and _is_initial_terminal_transition(
                authoritative,
                candidate,
            )
            and not _initial_source_finalization_publication(
                authoritative,
                candidate,
            )
            and not _initial_owner_terminal_publication(
                authoritative,
                candidate,
            )
            and not _initial_task_admin_archive_publication(
                authoritative,
                candidate,
            )
        ):
            raise ValueError("recovery_source_finalization_write_authority_required")
        if _source_post_commit_progression_candidate(
            authoritative,
            candidate,
        ) and not _source_post_commit_progression_publication(
            authoritative,
            candidate,
        ):
            raise ValueError("recovery_source_post_commit_write_authority_required")
        child_terminal_transition = bool(
            _is_recovery_child(authoritative)
            and _is_initial_terminal_transition(
                authoritative,
                candidate,
            )
        )
        if (
            child_terminal_transition
            and not _initial_execute_result_acceptance_publication(
                authoritative,
                candidate,
            )
            and not _initial_dispatch_abort_publication(
                authoritative,
                candidate,
            )
            and not _initial_owner_terminal_publication(
                authoritative,
                candidate,
            )
            and not _initial_dependency_reconciliation_publication(
                authoritative,
                candidate,
            )
            and not _initial_child_cancellation_publication(
                authoritative,
                candidate,
            )
        ):
            raise ValueError("recovery_result_commit_write_authority_required")
        if _initial_execute_result_acceptance_candidate(
            authoritative,
            candidate,
        ) and not _initial_execute_result_acceptance_publication(
            authoritative,
            candidate,
        ):
            raise ValueError("recovery_result_commit_write_authority_required")
        if not _candidate_matches_accepted_execute_result(
            authoritative,
            candidate,
        ) and not _initial_dispatch_abort_publication(
            authoritative,
            candidate,
        ):
            logging.warning(
                "Rejected mutation of accepted Recovery result %s",
                task_id,
            )
            return None

    authoritative_status = str(authoritative.status or "").strip().lower()
    candidate_status = str(getattr(candidate, "status", "") or "").strip().lower()
    authoritative_source_id = str(authoritative.source_task_id or "").strip()
    if authoritative_source_id and authoritative_source_id not in lock_ids:
        raise RuntimeError("recovery_task_source_fence_changed:" + task_id)
    source = session.get(TaskDB, authoritative_source_id) if authoritative_source_id else None
    goal_id = str(authoritative.goal_id or "").strip()
    goal = session.get(GoalDB, goal_id) if goal_id else None
    owner_terminal = bool(
        (source is not None and str(source.status or "").strip().lower() in _TERMINAL_TASK_STATUSES)
        or (
            goal is not None
            and str(goal.status or "").strip().lower()
            in {
                "completed",
                "failed",
                "cancelled",
                "aborted",
                "timeout",
                "archived",
            }
        )
    )
    if (
        owner_terminal
        and candidate_status != authoritative_status
        and candidate_status
        not in {
            "cancelled",
            "failed",
            "verification_failed",
            "aborted",
            "timeout",
        }
    ):
        logging.warning(
            "Rejected recovery-task save %s after owner terminal",
            task_id,
        )
        return None
    if (
        authoritative_status in _TERMINAL_TASK_STATUSES
        and candidate_status != authoritative_status
        and not (
            authoritative_status == "completed"
            and candidate_status == "verification_failed"
            and str(
                getattr(
                    candidate,
                    "status_reason_code",
                    "",
                )
                or ""
            )
            == "recovery_result_verification_failed"
        )
    ):
        logging.warning(
            "Rejected stale recovery-task save %s: %s -> %s",
            task_id,
            authoritative_status,
            candidate_status,
        )
        return None
    if _task_recovery_lifecycle_rank(authoritative) > _task_recovery_lifecycle_rank(candidate):
        candidate.status = authoritative.status
        candidate.status_reason_code = authoritative.status_reason_code

    if _source_finalization_publication_valid(authoritative):
        for field in (
            "last_output",
            "last_exit_code",
            "callback_url",
            "callback_token",
            "parent_task_id",
            "current_worker_job_id",
        ):
            setattr(
                candidate,
                field,
                copy.deepcopy(getattr(authoritative, field, None)),
            )

    candidate_updated_at = float(getattr(candidate, "updated_at", 0.0) or 0.0)
    authoritative_updated_at = float(authoritative.updated_at or 0.0)
    if candidate_updated_at < authoritative_updated_at:
        logging.warning(
            "Rejected stale recovery-task revision %s: %.9f < %.9f",
            task_id,
            candidate_updated_at,
            authoritative_updated_at,
        )
        return None

    candidate.status_reason_details = _merge_recovery_details(
        authoritative,
        candidate,
    )
    candidate.verification_status = _merge_recovery_verification(
        authoritative,
        candidate,
    )
    if authoritative_status in _TERMINAL_TASK_STATUSES and authoritative.status_reason_code:
        candidate.status_reason_code = authoritative.status_reason_code
    candidate.updated_at = max(
        time.time(),
        candidate_updated_at,
        authoritative_updated_at,
    )
    return candidate


def _preserve_bound_knowledge_index_context(
    authoritative: TaskDB,
    candidate: TaskDB,
    *,
    write_operation: str,
) -> TaskDB:
    """Merge generic context writes without weakening a bound index job.

    Knowledge-index execution envelopes are Hub authority records.  Generic
    task writers may add independent context keys, but they must not erase or
    replace a newer bound envelope, assignment, lifecycle state or dispatch
    policy from a stale detached ``TaskDB`` instance.  The repository-owned
    status CAS remains the only general status transition seam.
    """

    authoritative_context = copy.deepcopy(
        dict(authoritative.worker_execution_context or {})
    )
    authoritative_job = authoritative_context.get("knowledge_index_job")
    if not isinstance(authoritative_job, dict) or authoritative_job.get(
        "schema"
    ) != "ananta.knowledge_index_execution_job.v2":
        return candidate
    if write_operation == "save":
        candidate.status = authoritative.status
    candidate.assigned_agent_url = authoritative.assigned_agent_url
    candidate.task_kind = authoritative.task_kind
    candidate_context = copy.deepcopy(
        dict(candidate.worker_execution_context or {})
    )
    for reserved_key in (
        "knowledge_index_job",
        "knowledge_index_worker_binding",
        "destination_selection",
        "source_access_intent",
    ):
        if reserved_key in authoritative_context:
            candidate_context[reserved_key] = copy.deepcopy(
                authoritative_context[reserved_key]
            )
        else:
            candidate_context.pop(reserved_key, None)
    # Replay authority lives exclusively in the Worker-scoped SQL ledger.
    # Never accept or perpetuate a generic Task-context receipt.
    authoritative_context.pop(
        "knowledge_index_dispatch_receipt",
        None,
    )
    candidate_context.pop(
        "knowledge_index_dispatch_receipt",
        None,
    )
    candidate.worker_execution_context = {
        **authoritative_context,
        **candidate_context,
    }
    return candidate


def _engine():
    from agent.database import engine

    return engine


class TaskRepository:
    def get_all(self):
        with Session(_engine()) as session:
            return session.exec(select(TaskDB)).all()

    def get_by_id(self, task_id: str) -> Optional[TaskDB]:
        with Session(_engine()) as session:
            return session.get(TaskDB, task_id)

    def list_stale_reserved_unsloth_cleanup(
        self,
        *,
        before: float,
        limit: int,
    ) -> List[TaskDB]:
        bounded = max(1, min(int(limit), 500))
        with Session(_engine()) as session:
            statement = (
                select(TaskDB)
                .where(
                    TaskDB.status == "reserved",
                    TaskDB.task_kind == "ml.storage.cleanup",
                    TaskDB.created_at <= float(before),
                )
                .order_by(TaskDB.created_at.asc(), TaskDB.id.asc())
                .limit(bounded)
            )
            return list(session.exec(statement).all())

    def get_by_goal_id(self, goal_id: str) -> List[TaskDB]:
        with Session(_engine()) as session:
            return session.exec(select(TaskDB).where(TaskDB.goal_id == goal_id)).all()

    def save(self, task: TaskDB):
        task_id = str(getattr(task, "id", "") or "").strip()
        if not task_id:
            raise ValueError("task_id_required")
        from agent.common.recovery_result_write_boundary import (
            defer_task_repository_save,
        )

        if defer_task_repository_save(task_id, task=task):
            return self.get_by_id(task_id) or task
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        # Resolve the immutable owner hint before taking locks.  Recovery
        # writers and terminal sweeps then acquire the identical sorted
        # child/source pair; neither can hold the source and wait on a child.
        with Session(_engine()) as hint_session:
            authoritative_hint = hint_session.get(TaskDB, task_id)
            source_task_id = str(
                getattr(
                    authoritative_hint,
                    "source_task_id",
                    None,
                )
                or getattr(task, "source_task_id", None)
                or ""
            ).strip()
        lock_ids = {task_id}
        if source_task_id:
            lock_ids.add(source_task_id)
        with get_task_mutation_lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                raise RuntimeError(f"task_mutation_lock_unavailable:{task_id}")
            with Session(_engine()) as session:
                statement = select(TaskDB).where(TaskDB.id == task_id)
                if str(_engine().dialect.name or "").lower() == "postgresql":
                    statement = statement.with_for_update()
                authoritative = session.exec(statement).one_or_none()
                if authoritative is None:
                    task = _apply_organization_workflow_completion_policy(
                        None,
                        task,
                        session=session,
                    )
                    persisted = session.merge(task)
                    session.commit()
                    session.refresh(persisted)
                    return persisted
                prepared = _prepare_existing_task_write(
                    authoritative,
                    task,
                    session=session,
                    lock_ids=lock_ids,
                    write_operation="save",
                )
                if prepared is None:
                    return authoritative
                task = prepared

                persisted = session.merge(task)
                session.commit()
                session.refresh(persisted)
                return persisted

    def replace_bound_knowledge_index_envelope(
        self,
        task_id: str,
        *,
        expected_envelope: dict,
        replacement_envelope: dict,
    ) -> TaskDB:
        """Atomically replace one Hub-bound index envelope.

        The focused merge keeps unrelated ``worker_execution_context`` keys
        written by concurrent services and provides an optimistic conflict
        boundary for changes to the authoritative envelope itself.
        """

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("task_id_required")
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with get_task_mutation_lock_port().mutation_locks(
            {normalized_task_id}
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    "task_mutation_lock_unavailable:"
                    + normalized_task_id
                )
            with Session(_engine()) as session:
                statement = select(TaskDB).where(
                    TaskDB.id == normalized_task_id
                )
                if (
                    str(_engine().dialect.name or "").lower()
                    == "postgresql"
                ):
                    statement = statement.with_for_update()
                task = session.exec(statement).one_or_none()
                if task is None:
                    raise ValueError("knowledge_index_job_not_found")
                context = copy.deepcopy(
                    dict(task.worker_execution_context or {})
                )
                current_envelope = context.get("knowledge_index_job")
                if current_envelope == replacement_envelope:
                    return task
                if current_envelope != expected_envelope:
                    raise ValueError(
                        "knowledge_index_execution_queue_context_conflict"
                    )
                context["knowledge_index_job"] = copy.deepcopy(
                    replacement_envelope
                )
                task.worker_execution_context = context
                task.updated_at = max(
                    time.time(),
                    float(task.updated_at or 0.0),
                )
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

    def upsert_bound_knowledge_index_worker_snapshot(
        self,
        task_id: str,
        *,
        status: str,
        base_envelope: dict,
        worker_binding: dict,
    ) -> TaskDB:
        """Persist a capability-free Hub snapshot in an isolated Worker DB."""

        normalized_task_id = str(task_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        assignment = base_envelope.get("assignment")
        if (
            not normalized_task_id
            or not normalized_status
            or normalized_status in _TERMINAL_TASK_STATUSES
            or str(base_envelope.get("schema") or "")
            != "ananta.knowledge_index_execution_job.v2"
            or str(base_envelope.get("job_id") or "")
            != normalized_task_id
            or "source_access_enforcement_manifest" in base_envelope
            or not isinstance(assignment, dict)
            or set(worker_binding)
            != {"schema", "worker_id", "worker_url"}
            or worker_binding.get("schema")
            != "ananta.knowledge_index_worker_binding.v1"
            or str(worker_binding.get("worker_id") or "")
            != str(assignment.get("worker_id") or "")
            or not str(worker_binding.get("worker_url") or "").strip()
        ):
            raise ValueError(
                "knowledge_index_task_snapshot_persistence_invalid"
            )
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with get_task_mutation_lock_port().mutation_locks(
            {normalized_task_id}
        ) as acquired:
            if not acquired:
                raise RuntimeError(
                    "task_mutation_lock_unavailable:"
                    + normalized_task_id
                )
            with Session(_engine()) as session:
                statement = select(TaskDB).where(
                    TaskDB.id == normalized_task_id
                )
                if (
                    str(_engine().dialect.name or "").lower()
                    == "postgresql"
                ):
                    statement = statement.with_for_update()
                task = session.exec(statement).one_or_none()
                if task is None:
                    task = TaskDB(
                        id=normalized_task_id,
                        status=normalized_status,
                        task_kind="codecompass_index_build",
                        assigned_agent_url=str(
                            worker_binding["worker_url"]
                        ).strip().rstrip("/"),
                        worker_execution_context={
                            "knowledge_index_job": copy.deepcopy(
                                base_envelope
                            ),
                            "knowledge_index_worker_binding": (
                                copy.deepcopy(worker_binding)
                            ),
                        },
                    )
                else:
                    if str(task.task_kind or "").strip().lower() != (
                        "codecompass_index_build"
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_task_mismatch"
                        )
                    if str(task.status or "").strip().lower() in (
                        _TERMINAL_TASK_STATUSES
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_task_terminal"
                        )
                    context = copy.deepcopy(
                        dict(task.worker_execution_context or {})
                    )
                    current_job = context.get("knowledge_index_job")
                    current_base = (
                        copy.deepcopy(dict(current_job))
                        if isinstance(current_job, dict)
                        else {}
                    )
                    current_base.pop(
                        "source_access_enforcement_manifest",
                        None,
                    )
                    assigned_url = str(
                        task.assigned_agent_url or ""
                    ).strip().rstrip("/")
                    expected_url = str(
                        worker_binding["worker_url"]
                    ).strip().rstrip("/")
                    existing_binding = context.get(
                        "knowledge_index_worker_binding"
                    )
                    if (
                        current_base != base_envelope
                        or assigned_url != expected_url
                        or (
                            existing_binding is not None
                            and existing_binding != worker_binding
                        )
                    ):
                        raise ValueError(
                            "knowledge_index_task_snapshot_authority_conflict"
                        )
                    if existing_binding is None:
                        if str(task.status or "").strip().lower() != (
                            normalized_status
                        ):
                            raise ValueError(
                                "knowledge_index_task_snapshot_status_conflict"
                            )
                        # A distributed Worker can share the Hub PostgreSQL
                        # database. Existing Hub Task rows are validation-only:
                        # do not add Worker projection keys or touch updated_at.
                        return task
                    # An isolated Worker stores a minimal projection only. A
                    # fresh Hub snapshot is authoritative for its lifecycle
                    # status and replaces transient local context additions.
                    task.status = normalized_status
                    task.assigned_agent_url = expected_url
                    task.worker_execution_context = {
                        "knowledge_index_job": copy.deepcopy(
                            base_envelope
                        ),
                        "knowledge_index_worker_binding": copy.deepcopy(
                            worker_binding
                        ),
                    }
                task.updated_at = max(
                    time.time(),
                    float(task.updated_at or 0.0),
                )
                session.add(task)
                session.commit()
                session.refresh(task)
                return task

    def compare_and_set_status(
        self,
        task_id: str,
        *,
        expected_statuses: set[str],
        target_status: str,
        predicate: Callable[[TaskDB], bool] | None = None,
        mutate: Callable[[TaskDB], None] | None = None,
    ) -> TaskStatusCompareAndSetResult:
        """Atomically validate and commit one existing Task status mutation."""

        normalized_task_id = str(task_id or "").strip()
        normalized_target = str(target_status or "").strip().lower()
        normalized_expected = {
            str(value or "").strip().lower() for value in expected_statuses if str(value or "").strip()
        }
        if not normalized_task_id or not normalized_target or not normalized_expected:
            return TaskStatusCompareAndSetResult(
                updated=False,
                task=None,
                previous_status=None,
            )
        from agent.common.task_mutation_lock import (
            get_task_mutation_lock_port,
        )

        with Session(_engine()) as hint_session:
            authoritative_hint = hint_session.get(
                TaskDB,
                normalized_task_id,
            )
            source_task_id = str(
                getattr(
                    authoritative_hint,
                    "source_task_id",
                    None,
                )
                or ""
            ).strip()
        lock_ids = {normalized_task_id}
        if source_task_id:
            lock_ids.add(source_task_id)
        with get_task_mutation_lock_port().mutation_locks(lock_ids) as acquired:
            if not acquired:
                return TaskStatusCompareAndSetResult(
                    updated=False,
                    task=None,
                    previous_status=None,
                )
            with Session(_engine()) as session:
                statement = select(TaskDB).where(TaskDB.id == normalized_task_id)
                if str(_engine().dialect.name or "").lower() == "postgresql":
                    statement = statement.with_for_update()
                authoritative = session.exec(statement).one_or_none()
                if authoritative is None:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=None,
                        previous_status=None,
                    )
                previous_status = str(authoritative.status or "").strip().lower()
                if previous_status not in normalized_expected:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                if predicate is not None and not predicate(authoritative):
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                # Persisted rows may contain legacy JSON nulls for fields whose
                # current model default is a list.  Re-validating the ORM dump
                # would reject such an otherwise authoritative row before the
                # CAS policy can compare it.  The detached copy preserves the
                # exact row values without copying SQLAlchemy Session state;
                # closed delta checks still reject unauthorized mutation.
                candidate = _detached_task_row_copy(authoritative)
                candidate.status = normalized_target
                mutation_timestamp = time.time()
                candidate.updated_at = mutation_timestamp
                if mutate is not None:
                    mutate(candidate)
                # Repository-owned revision time is not caller-mutable.
                candidate.updated_at = mutation_timestamp
                if (
                    str(candidate.id or "").strip() != normalized_task_id
                    or str(candidate.status or "").strip().lower() != normalized_target
                ):
                    raise ValueError("task_status_cas_candidate_binding_invalid")
                prepared = _prepare_existing_task_write(
                    authoritative,
                    candidate,
                    session=session,
                    lock_ids=lock_ids,
                    write_operation="status_cas",
                )
                if prepared is None:
                    return TaskStatusCompareAndSetResult(
                        updated=False,
                        task=authoritative,
                        previous_status=previous_status,
                    )
                persisted = session.merge(prepared)
                session.commit()
                session.refresh(persisted)
                return TaskStatusCompareAndSetResult(
                    updated=(str(persisted.status or "").strip().lower() == normalized_target),
                    task=persisted,
                    previous_status=previous_status,
                )

    def delete(self, task_id: str):
        with Session(_engine()) as session:
            task = session.get(TaskDB, task_id)
            if task:
                session.delete(task)
                session.commit()
                return True
            return False

    def clear_team_assignments(self, team_id: str) -> int:
        with Session(_engine()) as session:
            statement = select(TaskDB).where(TaskDB.team_id == team_id)
            tasks = session.exec(statement).all()
            from agent.services.recovery_task_mutation_policy import (
                ensure_external_recovery_mutation_allowed,
            )

            for task in tasks:
                ensure_external_recovery_mutation_allowed(
                    task,
                    action="team_detach",
                )
            for task in tasks:
                task.team_id = None
                session.add(task)
            session.commit()
            return len(tasks)

    def get_old_tasks(self, cutoff: float):
        with Session(_engine()) as session:
            statement = select(TaskDB).where(TaskDB.created_at < cutoff)
            return session.exec(statement).all()

    def get_paged(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str = None,
        status_values: list[str] | None = None,
        agent: str = None,
        since: float = None,
        until: float = None,
        tenant_id: str | None = None,
        project_id: str | None = None,
    ):
        with Session(_engine()) as session:
            statement = select(TaskDB)
            if status:
                statement = statement.where(TaskDB.status == status)
            elif status_values:
                statement = statement.where(or_(*[TaskDB.status == val for val in status_values]))
            if agent:
                statement = statement.where(TaskDB.assigned_agent_url == agent)
            if since:
                statement = statement.where(TaskDB.created_at >= since)
            if until:
                statement = statement.where(TaskDB.created_at <= until)
            if tenant_id is not None:
                statement = statement.where(TaskDB.tenant_id == tenant_id)
            if project_id is not None:
                statement = statement.where(TaskDB.project_id == project_id)

            statement = (
                statement.order_by(
                    TaskDB.updated_at.desc(),
                    TaskDB.id.asc(),
                )
                .offset(offset)
                .limit(limit)
            )
            return session.exec(statement).all()


def _task_auxiliary_repository_dependencies() -> TaskAuxiliaryRepositoryDependencies:
    """Resolve patchable persistence dependencies at call time."""

    return TaskAuxiliaryRepositoryDependencies(
        session_factory=Session,
        select=select,
        delete=delete,
        archived_task_model=ArchivedTaskDB,
        agent_session_model=AgentSessionDB,
        tool_call_model=ToolCallDB,
        policy_snapshot_model=PolicySnapshotDB,
    )


class ArchivedTaskRepository(ArchivedTaskRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class AgentSessionRepository(AgentSessionRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class ToolCallRepository(ToolCallRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )


class PolicySnapshotRepository(PolicySnapshotRepositoryMixin):
    def __init__(self) -> None:
        super().__init__(
            lambda: _engine(),
            _task_auxiliary_repository_dependencies,
        )
