"""Atomic Hub publication of bound knowledge-index task results.

The execution binding and Source-Control projection are separate durable
aggregates.  This focused publisher is the only bridge that may terminalize a
bound v2 Hub task after those aggregates have accepted the result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from agent.services.hub_event_service import build_task_history_event

KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA = (
    "ananta.knowledge_index_execution_job.v2"
)
KNOWLEDGE_INDEX_TASK_RESULT_PUBLICATION_SCHEMA = (
    "ananta.knowledge_index_task_result_publication.v1"
)

_RECONCILABLE_TASK_STATUSES = frozenset(
    {
        "created",
        "todo",
        "blocked",
        "blocked_by_dependency",
        "proposing",
        "assigned",
        "in_progress",
        "running",
    }
)
_TERMINAL_RESULT_STATUSES = frozenset(
    {"completed", "failed", "cancelled"}
)
_STATUS_VALUE_FIELDS = frozenset(
    {
        "history",
        "last_output",
        "last_exit_code",
        "last_proposal",
        "verification_status",
    }
)


class BoundKnowledgeIndexTaskRepositoryPort(Protocol):
    def compare_and_set_status(
        self,
        task_id: str,
        **options: Any,
    ) -> Any: ...


class KnowledgeIndexTaskResultPublicationError(RuntimeError):
    """A Hub-owned result could not be projected into its bound Task."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _task_mapping(task: Any) -> dict[str, Any]:
    if hasattr(task, "model_dump"):
        return dict(task.model_dump())
    if isinstance(task, Mapping):
        return dict(task)
    return dict(vars(task))


def _bound_envelope(task: Any) -> dict[str, Any]:
    raw = _task_mapping(task)
    context = raw.get("worker_execution_context")
    envelope = (
        context.get("knowledge_index_job")
        if isinstance(context, Mapping)
        else None
    )
    if not isinstance(envelope, Mapping):
        return {}
    normalized = copy.deepcopy(dict(envelope))
    # The signed dispatch-only manifest is a later envelope augmentation.  It
    # does not alter the immutable base execution authority being published.
    normalized.pop("source_access_enforcement_manifest", None)
    return normalized


def _projection_value(projection: Any, field: str) -> Any:
    if isinstance(projection, Mapping):
        return projection.get(field)
    return getattr(projection, field, None)


class KnowledgeIndexTaskResultPublisher:
    """Publish result, status and history in one repository-owned CAS."""

    def __init__(
        self,
        *,
        repository: BoundKnowledgeIndexTaskRepositoryPort,
        execution_binding_service: Any,
        post_commit: Callable[..., Any] | None = None,
    ) -> None:
        self._repository = repository
        self._execution_binding_service = execution_binding_service
        self._post_commit = post_commit

    def publish(
        self,
        *,
        job_id: str,
        expected_envelope: Mapping[str, Any],
        result: Mapping[str, Any],
        status_values: Mapping[str, Any] | None = None,
        status_reason_code: str | None = None,
        event_type: str,
        event_actor: str,
        event_details: Mapping[str, Any] | None = None,
    ) -> Any:
        """Atomically terminalize one exact v2 task, or accept exact replay."""

        normalized_job_id = str(job_id or "").strip()
        normalized_result = copy.deepcopy(dict(result))
        target_status = str(
            normalized_result.get("status") or ""
        ).strip().lower()
        result_job_id = str(
            normalized_result.get("job_id") or normalized_job_id
        ).strip()
        base_envelope = copy.deepcopy(dict(expected_envelope))
        base_envelope.pop("source_access_enforcement_manifest", None)
        if (
            not normalized_job_id
            or result_job_id != normalized_job_id
            or target_status not in _TERMINAL_RESULT_STATUSES
            or base_envelope.get("schema")
            != KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA
            or str(base_envelope.get("job_id") or "")
            != normalized_job_id
        ):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_task_result_publication_binding_invalid"
            )

        values = copy.deepcopy(dict(status_values or {}))
        unknown_fields = set(values) - _STATUS_VALUE_FIELDS
        if unknown_fields:
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_task_result_publication_fields_unknown"
            )
        verification_patch = values.get("verification_status", {})
        if not isinstance(verification_patch, Mapping):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_task_result_verification_invalid"
            )
        forwarded_history_event = self._forwarded_history_event(
            values.get("history")
        )
        projection_digest = self._require_completion_projection(
            normalized_job_id,
            target_status=target_status,
        )
        result_digest = _canonical_digest(normalized_result)
        publication = {
            "schema": KNOWLEDGE_INDEX_TASK_RESULT_PUBLICATION_SCHEMA,
            "job_id": normalized_job_id,
            "status": target_status,
            "result_digest": result_digest,
        }
        if projection_digest is not None:
            publication["completion_projection_digest"] = (
                projection_digest
            )

        normalized_event_details = {
            **dict(event_details or {}),
            "knowledge_index_result_digest": result_digest,
        }

        def _matches_authority(task: Any) -> bool:
            if _bound_envelope(task) != base_envelope:
                return False
            # Returning false on an exact publication is the idempotent CAS
            # no-op; the postcondition below distinguishes it from conflict.
            return not self._has_publication(
                task,
                publication=publication,
                result=normalized_result,
            )

        def _mutate(task: Any) -> None:
            raw = _task_mapping(task)
            verification = copy.deepcopy(
                dict(raw.get("verification_status") or {})
            )
            verification.update(copy.deepcopy(dict(verification_patch)))
            verification["knowledge_index_job_result"] = copy.deepcopy(
                normalized_result
            )
            task.verification_status = verification

            details = copy.deepcopy(
                dict(raw.get("status_reason_details") or {})
            )
            details["knowledge_index_task_result_publication"] = (
                copy.deepcopy(publication)
            )
            task.status_reason_details = details
            task.status_reason_code = status_reason_code

            for field in (
                "last_output",
                "last_exit_code",
                "last_proposal",
            ):
                if field in values:
                    setattr(task, field, copy.deepcopy(values[field]))

            history = copy.deepcopy(list(raw.get("history") or []))
            if forwarded_history_event is not None:
                execution_event = copy.deepcopy(forwarded_history_event)
                execution_event[
                    "knowledge_index_result_digest"
                ] = result_digest
                history.append(execution_event)
            history.append(
                build_task_history_event(
                    task,
                    str(event_type),
                    actor=str(event_actor),
                    details=normalized_event_details,
                )
            )
            task.history = history[-200:]

        status_cas = getattr(
            self._repository,
            "compare_and_set_status",
            None,
        )
        if not callable(status_cas):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_atomic_task_status_repository_required"
            )
        outcome = status_cas(
            normalized_job_id,
            expected_statuses={
                *_RECONCILABLE_TASK_STATUSES,
                target_status,
            },
            target_status=target_status,
            predicate=_matches_authority,
            mutate=_mutate,
        )
        projected_task = getattr(outcome, "task", None)
        updated = bool(getattr(outcome, "updated", False))
        if not updated and not (
            projected_task is not None
            and self._has_publication(
                projected_task,
                publication=publication,
                result=normalized_result,
            )
        ):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_task_result_projection_conflict"
            )
        if updated and self._post_commit is not None:
            try:
                self._post_commit(
                    normalized_job_id,
                    old_status=getattr(
                        outcome,
                        "previous_status",
                        None,
                    ),
                    event_type=str(event_type),
                )
            except Exception:
                # Status/result/history are already one durable commit.  A
                # notification failure must not cause Worker redispatch.
                logging.exception(
                    "Knowledge-index task post-commit observation failed "
                    "for %s",
                    normalized_job_id,
                )
        return projected_task

    def _require_completion_projection(
        self,
        job_id: str,
        *,
        target_status: str,
    ) -> str | None:
        if target_status != "completed":
            return None
        getter = getattr(
            self._execution_binding_service,
            "get_completion_projection",
            None,
        )
        if not callable(getter):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_completion_projection_store_unavailable"
            )
        projection = getter(str(job_id))
        if str(_projection_value(projection, "state") or "") != (
            "projected"
        ):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_completion_projection_not_projected"
            )
        digest = str(
            _projection_value(projection, "projection_digest") or ""
        ).strip().lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_completion_projection_digest_invalid"
            )
        return digest

    @staticmethod
    def _forwarded_history_event(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise KnowledgeIndexTaskResultPublicationError(
                "knowledge_index_task_result_history_invalid"
            )
        for item in reversed(value):
            if (
                isinstance(item, Mapping)
                and str(item.get("event_type") or "")
                == "execution_result"
                and item.get("forwarded") is True
            ):
                return copy.deepcopy(dict(item))
        return None

    @staticmethod
    def _has_publication(
        task: Any,
        *,
        publication: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> bool:
        raw = _task_mapping(task)
        details = dict(raw.get("status_reason_details") or {})
        verification = dict(raw.get("verification_status") or {})
        return bool(
            str(raw.get("status") or "").strip().lower()
            == str(publication.get("status") or "")
            and details.get("knowledge_index_task_result_publication")
            == dict(publication)
            and verification.get("knowledge_index_job_result")
            == dict(result)
        )


__all__ = [
    "KNOWLEDGE_INDEX_TASK_RESULT_PUBLICATION_SCHEMA",
    "KnowledgeIndexTaskResultPublicationError",
    "KnowledgeIndexTaskResultPublisher",
]
