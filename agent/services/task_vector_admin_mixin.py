"""Reserved Vector-task administration adapter for ``TaskAdminService``.

The mixin keeps the generic task state machine independent from the dedicated
Vector lifecycle while preserving the public ``TaskAdminService`` API.
"""

from __future__ import annotations

from typing import Any

from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
    has_reserved_vector_index_marker,
)
from agent.services.vector_task_admin_guard_service import (
    require_authoritative_vector_task,
)


class TaskVectorAdminMixin:
    """Authorize and delegate mutations for the reserved Vector task domain."""

    @staticmethod
    def _vector_index_task_marker(task: Any) -> bool:
        """Recognize complete and partial markers so mutations fail closed."""

        return has_reserved_vector_index_marker(task)

    @staticmethod
    def _assert_authoritative_vector_index_task(task: Any) -> None:
        require_authoritative_vector_task(task)

    def _require_authorized_vector_index_task(
        self,
        task: Any,
        *,
        authorization: VectorAdminAuthorizationContext | None,
    ) -> bool:
        if not self._vector_index_task_marker(task):
            return False
        get_vector_store_authorization_policy().require_task_admin(
            authorization,
            task,
        )
        self._assert_authoritative_vector_index_task(task)
        return True

    def _intervene_vector_index_task(
        self,
        *,
        task: Any,
        action: str,
        actor: str,
        authorization: VectorAdminAuthorizationContext | None,
    ) -> tuple[bool, str, dict[str, Any]] | None:
        try:
            if not self._require_authorized_vector_index_task(
                task,
                authorization=authorization,
            ):
                return None
        except PermissionError as exc:
            reason = str(exc)
            return False, reason, {
                "reason_code": reason,
                "http_status": 403,
            }
        except ValueError as exc:
            reason = str(exc)
            return False, reason, {
                "reason_code": reason,
                "http_status": 409,
            }

        if action not in {"cancel", "retry"}:
            reason = "vector_index_task_intervention_forbidden"
            return False, reason, {
                "reason_code": reason,
                "action": action,
                "http_status": 409,
            }

        from agent.services.vector_index_task_service import (
            get_vector_index_task_service,
        )

        task_id = str(getattr(task, "id", "") or "")
        service = get_vector_index_task_service()
        try:
            result = (
                service.cancel(job_id=task_id, actor=actor)
                if action == "cancel"
                else service.retry(job_id=task_id, actor=actor)
            )
        except ValueError as exc:
            reason = str(exc)
            return False, reason, {
                "reason_code": reason,
                "http_status": (
                    404
                    if reason == "vector_index_task_not_found"
                    else 400
                ),
            }
        except RuntimeError as exc:
            reason = str(exc)
            return False, reason, {
                "reason_code": reason,
                "http_status": 409,
            }

        worker_cancel_forward = None
        if action == "cancel":
            from agent.services.request_cancellation_service import (
                get_request_cancellation_service,
            )

            worker_cancel_forward = (
                get_request_cancellation_service()
                .cancel_task_requests(
                    task_id=task_id,
                    include_workers=True,
                )
            )
        return True, "ok", {
            "id": task_id,
            "action": action,
            "status": str(result.get("status") or ""),
            **(
                {"worker_cancel_forward": worker_cancel_forward}
                if isinstance(worker_cancel_forward, dict)
                else {}
            ),
        }


__all__ = ["TaskVectorAdminMixin"]
