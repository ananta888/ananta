from __future__ import annotations

from client_surfaces.common.types import DegradedState


def map_status_to_degraded_state(status_code: int | None, *, parse_error: bool = False) -> DegradedState:
    if parse_error:
        return "malformed_response"
    if status_code is None:
        return "backend_unreachable"
    if 200 <= status_code < 300:
        return "healthy"
    if status_code == 401:
        return "auth_failed"
    if status_code == 403:
        return "policy_denied"
    if status_code == 422:
        return "capability_missing"
    if status_code >= 500:
        return "backend_unreachable"
    return "unknown_error"


def is_retriable_state(state: DegradedState) -> bool:
    return state in {"backend_unreachable", "malformed_response", "unknown_error"}
