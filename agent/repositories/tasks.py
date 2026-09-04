import copy
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from sqlalchemy import or_
from sqlmodel import Session, delete, select

from agent.common.recovery_task_merge_policy import (
    _merge_dispatch_lease as _merge_dispatch_lease,
)
from agent.common.recovery_task_merge_policy import (
    _merge_recovery_details,
    _merge_recovery_verification,
    _task_recovery_lifecycle_rank,
)
from agent.common.recovery_task_merge_policy import (
    _merge_recovery_source_post_commit as _merge_recovery_source_post_commit,
)
from agent.common.recovery_task_write_validation import (
    _TERMINAL_TASK_STATUSES,
    _candidate_matches_accepted_execute_result,
    _details,
    _initial_child_cancellation_publication,
    _initial_dependency_reconciliation_publication,
    _initial_dispatch_abort_publication,
    _initial_execute_result_acceptance_candidate,
    _initial_execute_result_acceptance_publication,
    _initial_owner_terminal_publication,
    _initial_source_finalization_publication,
    _initial_task_admin_archive_publication,
    _is_initial_terminal_transition,
    _is_recovery_child,
    _is_recovery_source,
    _is_recovery_task,
    _recovery_binding_mismatches,
    _recovery_execution_mismatches,
    _source_approval_rebind_attempt,
    _source_approval_rebind_publication,
    _source_finalization_publication_valid,
    _source_post_commit_progression_candidate,
    _source_post_commit_progression_publication,
)
from agent.db_models import (
    AgentSessionDB,
    ArchivedTaskDB,
    GoalDB,
    PolicySnapshotDB,
    TaskDB,
    ToolCallDB,
)
from agent.ports.task_completion_policy import TaskCompletionPolicyPort
from agent.repositories.task_auxiliary_repositories import (
    AgentSessionRepositoryMixin,
    ArchivedTaskRepositoryMixin,
    PolicySnapshotRepositoryMixin,
    TaskAuxiliaryRepositoryDependencies,
    ToolCallRepositoryMixin,
)


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


def _apply_task_completion_policy(
    authoritative: TaskDB | None,
    candidate: TaskDB,
    *,
    session: Session,
    completion_policy: TaskCompletionPolicyPort | None,
) -> TaskDB:
    """Apply the injected Hub policy or reject bound completion fail-closed."""

    if completion_policy is not None:
        return completion_policy.apply(
            authoritative_task=authoritative,
            candidate_task=candidate,
            session=session,
        )
    context = dict(getattr(candidate, "worker_execution_context", None) or {})
    if (
        str(getattr(candidate, "status", "") or "").strip().lower() == "completed"
        and isinstance(context.get("organization_workflow_step_binding"), dict)
    ):
        raise RuntimeError("organization_workflow_completion_policy_unavailable")
    return candidate


def _prepare_existing_task_write(
    authoritative: TaskDB,
    candidate: TaskDB,
    *,
    session: Session,
    lock_ids: set[str],
    write_operation: str,
    completion_policy: TaskCompletionPolicyPort | None,
) -> TaskDB | None:
    """Apply the single authoritative Recovery write policy in-transaction."""

    task_id = str(getattr(authoritative, "id", "") or "").strip()
    candidate = _preserve_bound_knowledge_index_context(
        authoritative,
        candidate,
        write_operation=write_operation,
    )
    candidate = _apply_task_completion_policy(
        authoritative,
        candidate,
        session=session,
        completion_policy=completion_policy,
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
    def __init__(self, *, completion_policy: TaskCompletionPolicyPort | None = None) -> None:
        self._completion_policy = completion_policy

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
                    task = _apply_task_completion_policy(
                        None,
                        task,
                        session=session,
                        completion_policy=self._completion_policy,
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
                    completion_policy=self._completion_policy,
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
                    completion_policy=self._completion_policy,
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
            from agent.common.recovery_task_mutation_policy import (
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
