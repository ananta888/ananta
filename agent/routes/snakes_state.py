"""Shared hub-owned state and request policy for snake route modules."""
from __future__ import annotations

import threading
from functools import wraps
from ipaddress import ip_address
from typing import Any

import jwt
from flask import Blueprint, g, request

from agent.auth import check_strict_auth
from agent.config import settings
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.identity_validation import IdentityValidationError
from agent.services.user_token_scope import (
    is_snake_events_stream_token,
    token_scope_allows_request,
)

snakes_bp = Blueprint("snakes", __name__)

_MAX_SNAKES = 8
_snakes: dict[str, dict[str, Any]] = {}
_messages: dict[str, list[dict[str, Any]]] = {}
_chat_messages: dict[str, list[dict[str, Any]]] = {}
_room_messages: list[dict[str, Any]] = []
_MAX_CHAT_MSGS = 500
_MAX_ROOM_MSGS = 200
_VALID_CHANNEL_TYPES = {"room", "direct", "ai", "system"}
_VALID_VISIBILITY = {"room", "direct", "ai_context", "system"}
_SCAN_CANCELS: dict[str, threading.Event] = {}
_VALID_ROLES = {"player", "tutor", "critic", "coach", "viewer"}
_VALID_COLORS = {"mint", "amber", "rose", "violet", "sky", "coral", "lime", "ice", "cyan"}


def _is_local_request() -> bool:
    """Return whether the explicit development-only loopback bypass applies.

    Private/RFC1918 and Docker bridge addresses are remote callers.  The
    bypass is fail-closed by default and is also disabled by the strict
    production workflow profile.
    """

    if (
        not settings.snake_local_dev_auth_bypass
        or settings.workflow_require_registered_worker_auth
    ):
        return False
    remote = request.remote_addr or ""
    try:
        ip = ip_address(remote)
        return ip.is_loopback
    except ValueError:
        return False


def _check_snake_control_auth(function):
    """Require a strict user/service bearer, with one explicit dev exception."""

    strict_function = check_strict_auth(function)

    @wraps(function)
    def wrapper(*args, **kwargs):
        if _is_local_request():
            g.user = {}
            g.auth_payload = {
                "auth_mode": "explicit_local_dev",
                "service_id": "local-dev",
            }
            g.is_admin = True
            return function(*args, **kwargs)
        return strict_function(*args, **kwargs)

    return wrapper


def _authenticated_snake_control_auth() -> dict[str, Any]:
    """Return a canonical principal context after control authentication."""

    user_auth = _optional_user_auth()
    if user_auth:
        return user_auth

    service_auth = dict(getattr(g, "auth_payload", {}) or {})
    # The static agent token sets sub to the literal "agent_token", which names
    # the authentication mode rather than an identity.  An owner principal has
    # to record who owns something, not how they proved it, so that marker is
    # skipped in favour of the agent's own name.
    subject = str(service_auth.get("sub") or "").strip()
    if subject == "agent_token":
        subject = ""
    service_id = str(
        service_auth.get("service_id")
        or service_auth.get("agent_id")
        or service_auth.get("worker_id")
        or subject
        or settings.agent_name
        or "hub"
    ).strip()
    if not service_id:
        return {}
    return {
        "sub": f"service:{service_id}",
        "tenant_id": "service:ananta",
        "role": "service",
        "auth_mode": str(service_auth.get("auth_mode") or "service_bearer"),
    }


def _optional_user_auth() -> dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if token.count(".") != 2:
        alt = str(request.headers.get("X-Ananta-User-Authorization") or "").strip()
        if not alt.startswith("Bearer "):
            return {}
        token = alt[7:].strip()
    if token.count(".") != 2:
        return {}
    try:
        payload = dict(jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30) or {})
        if not token_scope_allows_request(
            payload,
            method=request.method,
            path=request.path,
        ):
            return {}
        return payload
    except jwt.PyJWTError:
        return {}


def _snake_stream_query_auth(snake_id: str) -> dict[str, Any]:
    """Decode only a short-lived, purpose-bound Snake SSE derivative.

    Full user JWTs and long-lived Snake bearer credentials are deliberately
    rejected in query strings. Header-based user JWT authentication remains
    available through :func:`_optional_user_auth`.
    """

    token = str(request.args.get("stream_token") or "")
    if not token or token.count(".") != 2:
        return {}
    try:
        payload = dict(
            jwt.decode(
                token,
                settings.secret_key,
                algorithms=["HS256"],
                leeway=30,
            )
            or {}
        )
    except jwt.PyJWTError:
        return {}
    if not is_snake_events_stream_token(payload):
        return {}
    if str(payload.get("stream_snake_id") or "") != snake_id:
        return {}
    if not token_scope_allows_request(
        payload,
        method=request.method,
        path=request.path,
    ):
        return {}
    return payload


def _request_device_id() -> str:
    return str(request.headers.get("X-Ananta-Device-Id") or "").strip()


def _chat_principal_from_auth(auth: dict[str, Any]) -> ChatSessionPrincipal | None:
    subject = auth.get("sub") or auth.get("username") or ""
    tenant_id = (
        auth.get("tenant_id")
        or auth.get("tenant")
        or auth.get("organization_id")
        or subject
    )
    try:
        return ChatSessionPrincipal.from_values(tenant_id, subject)
    except (IdentityValidationError, ValueError):
        return None


def _snake_owner_principal(snake: dict[str, Any]) -> ChatSessionPrincipal | None:
    raw = snake.get("owner_principal")
    if isinstance(raw, dict):
        try:
            return ChatSessionPrincipal.from_values(raw.get("tenant_id"), raw.get("subject_id"))
        except (IdentityValidationError, ValueError):
            return None
    if "owner_principal" in snake:
        return None
    # Deterministic compatibility for registrations created before the
    # canonical principal field existed. Never infer ownership from the caller.
    subject = str(snake.get("oidc_id") or "").strip()
    if not subject and snake.get("auth_mode") == "legacy_local_dev":
        subject = str(settings.initial_admin_user or "").strip()
    if not subject:
        return None
    tenant_id = str(snake.get("tenant_id") or subject).strip()
    try:
        principal = ChatSessionPrincipal.from_values(tenant_id, subject)
    except (IdentityValidationError, ValueError):
        return None
    snake["owner_principal"] = principal.to_dict()
    return principal


def _snake_bound_to_auth(snake: dict[str, Any], auth: dict[str, Any]) -> bool:
    request_principal = _chat_principal_from_auth(auth)
    owner_principal = _snake_owner_principal(snake)
    if request_principal is None or owner_principal != request_principal:
        return False
    req_device = _request_device_id()
    snake_device = str(snake.get("owner_device_id") or "").strip()
    return not (req_device and snake_device and req_device != snake_device)


def _next_free_color() -> str:
    used = {snake.get("color") for snake in _snakes.values()}
    return next((color for color in _VALID_COLORS if color not in used), "mint")
