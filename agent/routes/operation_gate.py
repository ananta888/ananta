from __future__ import annotations

from functools import wraps

from flask import current_app, g

from agent.common.errors import api_response
from agent.common.logging import get_correlation_id
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.operation_policy_observability_service import (
    get_operation_policy_observability_service,
)
from agent.services.operation_policy_service import OperationAuthContext, get_operation_policy_service
from agent.services.operation_registry_service import get_operation_registry_service


def operation_gate(operation_id: str):
    """Opt-in Flask adapter. Authentication decorators must wrap this gate."""

    descriptor = get_operation_registry_service().require(operation_id)
    if descriptor.transport != "api":
        raise ValueError(f"rest_operation_required:{operation_id}")

    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            is_agent_auth = bool(getattr(g, "auth_payload", None))
            is_user_auth = bool(getattr(g, "user", None))
            auth_source = get_exposure_policy_service().resolve_auth_source(
                is_agent_auth=is_agent_auth,
                is_user_auth=is_user_auth,
            )
            auth = OperationAuthContext(
                auth_source=auth_source,
                is_admin=bool(getattr(g, "is_admin", False)),
                approval_granted=bool(getattr(g, "is_admin", False)),
            )
            service = get_operation_policy_service()
            policy = service.resolve_policy(current_app.config.get("AGENT_CONFIG", {}) or {})
            decision = service.decide(descriptor, policy, auth)
            get_operation_policy_observability_service().record(
                decision,
                trace_id=get_correlation_id(),
                surface="rest",
                emit_audit_event=bool(policy.get("emit_audit_events", True)),
            )
            if not decision.allowed:
                return api_response(
                    status="error",
                    message="operation_forbidden",
                    data={"reason_code": decision.reason_code, "trace_id": get_correlation_id()},
                    code=403,
                )
            return function(*args, **kwargs)

        wrapped.operation_id = operation_id
        return wrapped

    return decorator
