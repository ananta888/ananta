"""HTTP adapter for the route-independent Vector admin policy."""

from __future__ import annotations

from flask import g

from agent.auth import get_request_auth_context
from agent.common.errors import api_response
from agent.services.vector_store_authorization_policy import (
    VectorAdminAuthorizationContext,
    get_vector_store_authorization_policy,
)
from agent.services.vector_task_admin_guard_service import (
    generic_vector_mutation_error,
    get_vector_task_admin_guard_service,
)


def request_vector_authorization() -> (
    VectorAdminAuthorizationContext
):
    return get_vector_store_authorization_policy().from_identity(
        get_request_auth_context(),
        authenticated_admin=bool(
            getattr(g, "is_admin", False)
            and not (getattr(g, "user", {}) or {})
        ),
        source="task_management_route",
    )


def vector_permission_error(error: PermissionError):
    reason = str(error)
    return api_response(
        status="error",
        message=reason,
        data={"reason_code": reason},
        code=403,
    )


def guard_vector_control_mutation(task_id: str):
    try:
        get_vector_task_admin_guard_service().require_authorized_if_vector(
            task_id=task_id,
            authorization=request_vector_authorization(),
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except ValueError as exc:
        reason = str(exc)
        return api_response(
            status="error",
            message=reason,
            data={"reason_code": reason},
            code=409,
        )
    return None


def reserved_vector_mutation_response(task):
    """Reject a reserved Vector task at a generic HTTP mutation boundary."""

    result = generic_vector_mutation_error(task)
    if result is None:
        return None
    return api_response(
        status="error",
        message=result["error"],
        data=result["data"],
        code=result["code"],
    )


__all__ = [
    "guard_vector_control_mutation",
    "request_vector_authorization",
    "reserved_vector_mutation_response",
    "vector_permission_error",
]
