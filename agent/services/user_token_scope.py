"""Pure purpose and identity checks for restricted Hub user tokens.

Authentication adapters decode tokens with different transports, but they must
all apply the same purpose restriction.  Keeping this module independent from
Flask makes the rule reusable by HTTP and WebSocket boundaries and easy to
verify without request-local state.
"""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)

CONTROL_CENTER_STREAM_TOKEN_USE = "control_center_stream"
CONTROL_CENTER_STREAM_PATH = "/api/events/stream"
SNAKE_EVENTS_STREAM_TOKEN_USE = "snake_events_stream"


def is_control_center_stream_token(claims: Mapping[str, Any]) -> bool:
    """Return whether *claims* identify a restricted stream derivative."""

    return claims.get("token_use") == CONTROL_CENTER_STREAM_TOKEN_USE or claims.get("cc_stream") is True


def is_snake_events_stream_token(claims: Mapping[str, Any]) -> bool:
    """Return whether *claims* identify a restricted Snake SSE derivative."""

    return claims.get("token_use") == SNAKE_EVENTS_STREAM_TOKEN_USE


def snake_events_stream_identity_is_bound(claims: Mapping[str, Any]) -> bool:
    """Require an exact parent user/tenant and one canonical Snake ID."""

    if not is_snake_events_stream_token(claims):
        return False
    try:
        subject = require_canonical_identity(
            claims.get("sub") or claims.get("username"),
            field_name="stream_subject",
        )
        tenant_id = require_canonical_identity(
            claims.get("tenant_id"),
            field_name="stream_tenant_id",
        )
        stream_user_id = require_canonical_identity(
            claims.get("stream_user_id"),
            field_name="stream_user_id",
        )
        stream_tenant_id = require_canonical_identity(
            claims.get("stream_tenant_id"),
            field_name="stream_tenant_id",
        )
        require_canonical_identity(
            claims.get("stream_snake_id"),
            field_name="stream_snake_id",
        )
    except IdentityValidationError:
        return False
    return stream_user_id == subject and stream_tenant_id == tenant_id


def token_scope_allows_request(
    claims: Mapping[str, Any],
    *,
    method: str,
    path: str,
) -> bool:
    """Return whether token purpose permits one transport request."""

    if is_control_center_stream_token(claims):
        return str(method).upper() == "GET" and str(path) == CONTROL_CENTER_STREAM_PATH
    if is_snake_events_stream_token(claims):
        if not snake_events_stream_identity_is_bound(claims):
            return False
        snake_id = str(claims.get("stream_snake_id") or "")
        return (
            str(method).upper() == "GET"
            and str(path) == f"/snakes/{snake_id}/events/stream"
        )
    return True


def control_center_stream_identity_is_bound(claims: Mapping[str, Any]) -> bool:
    """Require a derivative token to retain its exact parent user and tenant."""

    if not is_control_center_stream_token(claims):
        return False
    try:
        subject = require_canonical_identity(
            claims.get("sub") or claims.get("username"),
            field_name="stream_subject",
        )
        tenant_id = require_canonical_identity(
            claims.get("tenant_id"),
            field_name="stream_tenant_id",
        )
        stream_user_id = require_canonical_identity(
            claims.get("stream_user_id"),
            field_name="stream_user_id",
        )
        stream_tenant_id = require_canonical_identity(
            claims.get("stream_tenant_id"),
            field_name="stream_tenant_id",
        )
    except IdentityValidationError:
        return False
    return stream_user_id == subject and stream_tenant_id == tenant_id


__all__ = [
    "CONTROL_CENTER_STREAM_PATH",
    "CONTROL_CENTER_STREAM_TOKEN_USE",
    "SNAKE_EVENTS_STREAM_TOKEN_USE",
    "control_center_stream_identity_is_bound",
    "is_control_center_stream_token",
    "is_snake_events_stream_token",
    "snake_events_stream_identity_is_bound",
    "token_scope_allows_request",
]
