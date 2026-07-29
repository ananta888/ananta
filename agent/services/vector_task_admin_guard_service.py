"""Central guard for generic mutations targeting reserved Vector tasks."""

from __future__ import annotations

from collections.abc import Mapping

from agent.services.repository_registry import get_repository_registry
from agent.services.vector_index_task_contracts import (
    VectorIndexTrustedScope,
)
from agent.services.vector_index_task_ingress_policy import (
    reserved_vector_index_ingress_error,
)
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
    has_reserved_vector_index_marker,
    reserved_vector_index_marker,
    vector_task_payload,
)


def require_authoritative_vector_task(
    task,
) -> dict:
    """Validate the identity and trusted scope used by admin mutations."""

    from agent.services._vector_index_result_forwarding import (
        is_authoritative_vector_index_task,
    )

    raw = vector_task_payload(task)
    try:
        if not is_authoritative_vector_index_task(raw):
            raise ValueError
        context = raw.get("worker_execution_context")
        envelope = (
            context.get("vector_index_task")
            if isinstance(context, Mapping)
            else None
        )
        if not isinstance(envelope, Mapping):
            raise ValueError
        task_id = str(raw.get("id") or "").strip()
        job_id = str(envelope.get("job_id") or "").strip()
        if not task_id or job_id != task_id:
            raise ValueError
        scope = VectorIndexTrustedScope(
            **dict(envelope.get("scope") or {})
        )
        if (
            str(envelope.get("scope_fingerprint") or "")
            != scope.fingerprint()
        ):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "vector_index_task_domain_binding_invalid"
        ) from exc
    return raw


class VectorTaskAdminGuardService:
    """Authorize one existing task without depending on an HTTP route."""

    @staticmethod
    def require_authorized_tasks_if_vector(
        *,
        tasks,
        authorization: (
            VectorAdminAuthorizationContext | None
        ),
        global_scope: bool = False,
    ) -> int:
        reserved_tasks = [
            task
            for task in tasks
            if has_reserved_vector_index_marker(task)
        ]
        if not reserved_tasks:
            return 0
        policy = get_vector_store_authorization_policy()
        if global_scope:
            policy.require_global_admin(authorization)
        for task in reserved_tasks:
            if not global_scope:
                policy.require_task_admin(authorization, task)
            require_authoritative_vector_task(task)
        return len(reserved_tasks)

    def require_authorized_if_vector(
        self,
        *,
        task_id: str,
        authorization: (
            VectorAdminAuthorizationContext | None
        ),
    ) -> bool:
        task = get_repository_registry().task_repo.get_by_id(
            str(task_id or "")
        )
        if task is None or not has_reserved_vector_index_marker(
            task
        ):
            return False
        get_vector_store_authorization_policy().require_task_admin(
            authorization,
            task,
        )

        require_authoritative_vector_task(task)
        return True


def generic_vector_mutation_error(task):
    """Return the stable generic-boundary denial for a reserved task."""

    marker = reserved_vector_index_marker(task)
    return (
        reserved_vector_index_ingress_error(marker)
        if marker is not None
        else None
    )


_GUARD = VectorTaskAdminGuardService()


def get_vector_task_admin_guard_service() -> (
    VectorTaskAdminGuardService
):
    return _GUARD


__all__ = [
    "VectorTaskAdminGuardService",
    "generic_vector_mutation_error",
    "get_vector_task_admin_guard_service",
    "require_authoritative_vector_task",
]
