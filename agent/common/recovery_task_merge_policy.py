"""Infrastructure-free merge policy for persisted Hub-owned recovery state."""

from __future__ import annotations

import copy
import time
from typing import Any

from agent.common.recovery_task_write_validation import (
    _LEASE_ACCEPTANCE_FIELDS,
    _LEASE_ADMISSION_FIELDS,
    _LEASE_ARTIFACT_MANIFEST_BINDING,
    _LEASE_CANCELLATION_FIELDS,
    _LEASE_CAPABILITY_BINDING_FIELDS,
    _LEASE_NEXT_REVISION_STATES,
    _LEASE_REVOCATION_FIELDS,
    _LEASE_SAME_REVISION_TRANSITIONS,
    _LEASE_STABLE_BINDING_FIELDS,
    _RECOVERY_POST_COMMIT_BINDING_FIELDS,
    _RECOVERY_POST_COMMIT_FIELDS,
    _RECOVERY_STATE_ORDER,
    _attempt_count,
    _details,
    _initial_child_cancellation_publication,
    _initial_dependency_reconciliation_publication,
    _initial_owner_terminal_publication,
    _initial_source_finalization_publication,
    _lease_fields_match,
    _lease_is_expired,
    _lease_revision,
    _lease_state,
    _positive_timestamp,
    _sha256_hex,
    _source_approval_rebind_publication,
)


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
