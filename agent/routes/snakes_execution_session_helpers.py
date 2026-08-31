"""Session-bound input projections for snake chat execution routes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent.config import settings
from agent.services.chat_session_security import (
    ChatSessionPrincipal,
    authorize_session,
    chat_session_mutation_lock,
)


def normalize_client_context_history(raw_history: Any) -> list[dict[str, str]] | None:
    """Validate the optional browser-controlled continuation context."""

    if raw_history is None:
        return None
    if not isinstance(raw_history, list):
        raise ValueError("context_history muss eine Liste sein")
    normalized: list[dict[str, str]] = []
    for item in raw_history[-20:]:
        if not isinstance(item, dict):
            raise ValueError("context_history Eintraege muessen Objekte sein")
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            raise ValueError("ungueltiger context_history Eintrag")
        normalized.append({"role": role, "content": content[:2000]})
    return normalized


def owned_chat_session_snapshot(
    session_id: str,
    principal: ChatSessionPrincipal,
) -> dict[str, Any] | None:
    """Resolve one exact session before background execution starts."""

    from client_surfaces.operator_tui.config.user_config_manager import get_manager

    with chat_session_mutation_lock:
        manager = get_manager()
        stored = manager.load()
        raw_sessions = stored.get("chat_sessions")
        sessions = raw_sessions if isinstance(raw_sessions, list) else []
        if not sessions:
            from client_surfaces.operator_tui.chat_state import default_conversations

            sessions = default_conversations()
        session = next(
            (
                item
                for item in sessions
                if isinstance(item, dict) and str(item.get("id") or "") == session_id
            ),
            None,
        )
        if session is None:
            return None
        try:
            legacy_owner = ChatSessionPrincipal.from_values(
                settings.initial_admin_user,
                settings.initial_admin_user,
            )
        except ValueError:
            legacy_owner = None
        authorized, migrated = authorize_session(
            session,
            principal,
            legacy_default_owner=legacy_owner,
        )
        if not authorized:
            return None
        if migrated and not manager.save({"chat_sessions": sessions}):
            return None
        return deepcopy(session)


def public_snake_message(message: dict[str, Any]) -> dict[str, Any]:
    result = dict(message)
    result.pop("owner_principal", None)
    return result
