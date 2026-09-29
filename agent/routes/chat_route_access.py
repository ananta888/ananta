"""Chat request principal, record ownership and authorization guards."""

from __future__ import annotations

import copy
import logging
from functools import wraps
from typing import Any

from flask import (
    current_app,
    jsonify,
)

from agent.auth import get_request_auth_context
from agent.config import settings as agent_settings
from agent.services.chat_organization_service import (
    ChatOrganizationService,
    OrganizationError,
)
from agent.services.chat_process_binding import public_graph as public_process_graph
from agent.services.chat_session_security import (
    ChatSessionPrincipal,
    authorize_session,
    chat_session_mutation_lock,
)
from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)
from client_surfaces.operator_tui.chat_state import (
    get_session,
    get_sessions,
)


def _chat_module():
    """Resolve monkeypatch seams through the public ``agent.routes.chat`` module at call time.

    Tests patch collaborators such as service getters on ``agent.routes.chat``;
    looking them up lazily keeps those patches effective for code that
    now lives in sibling modules (and avoids an import-time cycle).
    """
    import importlib

    return importlib.import_module("agent.routes.chat")


_log = logging.getLogger("agent.routes.chat")


def _organization_service() -> ChatOrganizationService:
    return ChatOrganizationService(_chat_module().get_manager())


def _organization_error(exc: OrganizationError):
    return jsonify(exc.payload()), exc.status


def _chat_workflow_principal() -> ChatSessionPrincipal | None:
    identity = get_request_auth_context()
    if not identity:
        return ChatSessionPrincipal("test-user", "test-user") if current_app.testing else None
    if isinstance(identity, dict):
        subject = identity.get("sub") or identity.get("username") or ""
        tenant_id = (
            identity.get("tenant_id")
            or identity.get("tenant")
            or identity.get("organization_id")
            or subject
        )
    else:
        subject = getattr(identity, "username", "") or getattr(identity, "id", "")
        tenant_id = (
            getattr(identity, "tenant_id", "")
            or getattr(identity, "organization_id", "")
            or subject
        )
    try:
        return ChatSessionPrincipal(
            tenant_id=require_canonical_identity(tenant_id, field_name="tenant_id"),
            subject_id=require_canonical_identity(subject, field_name="subject_id"),
        )
    except IdentityValidationError:
        return None


def _chat_workflow_run_is_owned_by(
    run: dict[str, Any],
    principal: ChatSessionPrincipal,
) -> bool:
    """Apply one fail-closed ownership rule to every chat workflow read/control path."""

    control_principal = run.get("control_principal")
    if not isinstance(control_principal, dict):
        return False
    return (
        control_principal.get("tenant_id") == principal.tenant_id
        and control_principal.get("subject_id") == principal.subject_id
    )


def _public_process_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    for key in ("graph", "graph_snapshot"):
        graph = result.get(key)
        if isinstance(graph, dict):
            result[key] = public_process_graph(graph)
    return result


def _legacy_chat_owner() -> ChatSessionPrincipal | None:
    """Map pre-ownership chat data to the configured original local admin."""

    try:
        return ChatSessionPrincipal.from_values(
            agent_settings.initial_admin_user,
            agent_settings.initial_admin_user,
        )
    except (IdentityValidationError, ValueError):
        return None


def _owned_session(
    chat: dict[str, Any],
    session_id: str,
    principal: ChatSessionPrincipal,
) -> tuple[dict[str, Any] | None, bool]:
    session = get_session(chat, session_id)
    if session is None:
        return None, False
    authorized, migrated = authorize_session(
        session,
        principal,
        legacy_default_owner=_legacy_chat_owner(),
    )
    if authorized:
        # Normalize only the authorized record. ``get_sessions`` performs
        # in-place legacy migrations and must never touch another principal's
        # records as a side effect of this request.
        get_sessions({"ai_sessions": [session]})
    return (session if authorized else None), migrated


def _owned_sessions(
    chat: dict[str, Any],
    principal: ChatSessionPrincipal,
) -> tuple[list[dict[str, Any]], bool]:
    owned: list[dict[str, Any]] = []
    migrated = False
    raw_sessions = chat.get("ai_sessions")
    sessions = raw_sessions if isinstance(raw_sessions, list) else []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        authorized, item_migrated = authorize_session(
            session,
            principal,
            legacy_default_owner=_legacy_chat_owner(),
        )
        migrated = migrated or item_migrated
        if authorized:
            get_sessions({"ai_sessions": [session]})
            owned.append(session)
    return owned, migrated


def _serialized_chat_mutation(view):
    @wraps(view)
    def serialized(*args, **kwargs):
        with chat_session_mutation_lock:
            return view(*args, **kwargs)

    return serialized


def _require_global_chat_admin(view):
    """Guard legacy global chat collections until they gain tenant storage."""

    @wraps(view)
    def authorized(*args, **kwargs):
        principal = _chat_workflow_principal()
        if principal is None:
            return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
        if principal != _legacy_chat_owner():
            return jsonify({"error": "global_chat_admin_required", "error_code": "global_chat_admin_required"}), 403
        return view(*args, **kwargs)

    return authorized
