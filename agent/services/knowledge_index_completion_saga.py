"""Hub-only completion saga for bound (v2) knowledge-index results.

After Worker artifacts are materialized the Hub durably accepts the result,
stages its completion projection, projects Source-Control state, activates
the materialized rows and marks the projection projected.  Every step after
the durable result CAS is Hub continuation work: failures surface as
``KnowledgeIndexCompletionProjectionPending`` and never re-dispatch Workers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.knowledge_index_job_ports import (
    KnowledgeIndexCompletionProjectionPending,
)


def require_transfer_deadline(transfer_deadline: Any | None) -> None:
    if transfer_deadline is not None:
        transfer_deadline.require_remaining_seconds()


class KnowledgeIndexCompletionSaga:
    """Finalize a bound result and apply its durable completion projection."""

    def __init__(
        self,
        *,
        execution_binding_service: Any | None,
        source_control_completion_projector: Any | None = None,
    ) -> None:
        self._execution_binding_service = execution_binding_service
        self._source_control_completion_projector = (
            source_control_completion_projector
        )

    def finalize(
        self,
        *,
        job_id: str,
        worker_result: Mapping[str, Any],
        materialized: dict[str, Any],
        task: Mapping[str, Any],
        artifact_service: Any,
        authenticated_worker_id: str | None = None,
        transfer_deadline: Any | None = None,
    ) -> dict[str, Any]:
        """Accept the result durably, then continue through the saga."""

        if self._execution_binding_service is None:
            raise RuntimeError(
                "knowledge_index_execution_binding_service_unavailable"
            )
        projection = None
        if str(materialized.get("status") or "") == "completed":
            if self._source_control_completion_projector is None:
                raise RuntimeError(
                    "knowledge_index_source_projection_service_unavailable"
                )
            complete_with_projection = getattr(
                self._execution_binding_service,
                "finalize_completed_result_with_projection",
                None,
            )
            if not callable(complete_with_projection):
                raise RuntimeError(
                    "knowledge_index_completion_projection_store_unavailable"
                )
            require_transfer_deadline(transfer_deadline)
            _record, projection = complete_with_projection(
                job_id=str(job_id),
                worker_result=worker_result,
                materialized_result=materialized,
                authenticated_worker_id=str(
                    authenticated_worker_id or ""
                ),
            )
        else:
            require_transfer_deadline(transfer_deadline)
            self._execution_binding_service.finalize_result(
                job_id=str(job_id),
                payload=worker_result,
                authenticated_worker_id=str(
                    authenticated_worker_id or ""
                ),
            )
        if projection is not None:
            try:
                require_transfer_deadline(transfer_deadline)
            except Exception as exc:
                # The completed-result CAS and its projection outbox are
                # durable. Continue through the Hub-only saga instead of
                # blaming or re-dispatching the Worker.
                raise KnowledgeIndexCompletionProjectionPending(
                    exc
                ) from exc
            return self.apply(
                projection=projection,
                task=task,
                artifact_service=artifact_service,
                transfer_deadline=transfer_deadline,
            )
        return materialized

    def apply(
        self,
        *,
        projection: Any,
        task: Mapping[str, Any],
        artifact_service: Any,
        transfer_deadline: Any | None = None,
    ) -> dict[str, Any]:
        payload = dict(projection.payload)
        materialized = dict(payload["materialized_result"])
        references = [
            dict(item)
            for item in list(payload.get("artifact_references") or [])
        ]
        context = dict(task.get("worker_execution_context") or {})
        envelope = dict(context.get("knowledge_index_job") or {})
        try:
            require_transfer_deadline(transfer_deadline)
            self._source_control_completion_projector.project(
                envelope=envelope,
                result=materialized,
                artifact_references=references,
            )
            require_transfer_deadline(transfer_deadline)
            activate = getattr(
                artifact_service,
                "activate_materialized_result",
                None,
            )
            if not callable(activate):
                raise RuntimeError(
                    "knowledge_index_worker_activation_service_unavailable"
                )
            activation_deadline = (
                {"transfer_deadline": transfer_deadline}
                if transfer_deadline is not None
                else {}
            )
            activated = activate(
                job_id=str(projection.job_id),
                result=materialized,
                artifact_references=references,
                task=task,
                **activation_deadline,
            )
            require_transfer_deadline(transfer_deadline)
            marker = getattr(
                self._execution_binding_service,
                "mark_completion_projection_projected",
                None,
            )
            if not callable(marker):
                raise RuntimeError(
                    "knowledge_index_completion_projection_store_unavailable"
                )
            marker(
                job_id=str(projection.job_id),
                expected_lock_version=int(projection.lock_version),
                expected_projection_digest=str(
                    projection.projection_digest
                ),
            )
            require_transfer_deadline(transfer_deadline)
            return activated
        except KnowledgeIndexCompletionProjectionPending:
            raise
        except Exception as exc:
            # Result CAS and the internal pending receipt are already durable.
            # The explicit Hub reconciler can replay this idempotent sequence
            # without another Worker dispatch, live lease, or mutable grant.
            raise KnowledgeIndexCompletionProjectionPending(exc) from exc


__all__ = [
    "KnowledgeIndexCompletionSaga",
    "require_transfer_deadline",
]
