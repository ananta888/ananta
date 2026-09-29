"""Load/save chat sessions, folders, profiles and chat types from the user config."""

from __future__ import annotations

from typing import Any

from agent.routes.chat_route_access import (
    _legacy_chat_owner,
    _owned_sessions,
)
from agent.routes.chat_route_dependencies import chat_route_dependencies
from agent.routes.chat_route_settings import (
    _migrate_profile_settings_v3,
    _migrate_session_settings_v3,
)
from agent.services.chat_session_security import (
    ChatSessionPrincipal,
    authorize_owned_record,
    chat_session_mutation_lock,
)
from agent.services.chat_setting_catalog import resolve_effective_settings
from client_surfaces.operator_tui.chat_state import (
    DEFAULT_CHAT_TYPES,
    default_chat_profiles,
    default_conversations,
)


def _load_chat(
    *,
    persist_migration: bool = False,
    principal: ChatSessionPrincipal | None = None,
) -> dict[str, Any]:
    """Build a minimal chat dict from persisted user.json for session operations."""
    dependencies = chat_route_dependencies()
    manager = dependencies.get_manager()
    settings = manager.load()
    sessions = settings.get("chat_sessions") or default_conversations()
    active_ids = settings.get("chat_active_session_ids")
    if principal is not None:
        # The historic global active-session pointer is not an ownership
        # boundary and must never select another user's chat implicitly.
        active_id = active_ids.get(principal.storage_key, "") if isinstance(active_ids, dict) else ""
    else:
        active_id = settings.get("chat_active_session_id") or (sessions[0].get("id", "") if sessions else "")
    chat = {"ai_sessions": sessions, "active_session_id": active_id, "channels": {}, "_preserve_session_list": True}
    owned_sessions: list[dict[str, Any]] = []
    owner_migrated = False
    if principal is not None:
        owned_sessions, owner_migrated = _owned_sessions(chat, principal)
        if not any(str(item.get("id") or "") == active_id for item in owned_sessions):
            chat["active_session_id"] = str(owned_sessions[0].get("id") or "") if owned_sessions else ""
    # The shared TUI migration only knows its built-in profiles. Re-resolve
    # persisted custom profiles at the HTTP persistence boundary so their
    # values cannot be replaced by compatibility defaults on reload.
    profiles_by_id = {str(profile.get("id") or ""): profile for profile in _load_profiles(principal)}
    for session in owned_sessions:
        profile = profiles_by_id.get(str(session.get("profile_id") or "general"))
        if profile is not None:
            _apply_profile(session, profile)
    settings_migrated = _migrate_session_settings_v3(owned_sessions)
    if persist_migration and (owner_migrated or settings_migrated):
        manager.save({"chat_sessions": chat["ai_sessions"], "chat_model_version": 3})
    return chat


def _save_chat(chat: dict[str, Any], *, principal: ChatSessionPrincipal | None = None) -> bool:
    """Persist sessions back to user.json."""
    dependencies = chat_route_dependencies()
    manager = dependencies.get_manager()
    payload: dict[str, Any] = {
        "chat_sessions": chat.get("ai_sessions") or [],
        "chat_active_session_id": chat.get("active_session_id") or "",
        "chat_model_version": 3,
    }
    if principal is not None:
        persisted = manager.load()
        active_ids = dict(persisted.get("chat_active_session_ids") or {})
        active_id = str(chat.get("active_session_id") or "")
        if active_id:
            active_ids[principal.storage_key] = active_id
        else:
            active_ids.pop(principal.storage_key, None)
        payload["chat_active_session_ids"] = active_ids
    return bool(manager.save(payload))


def _load_folders() -> list[dict]:
    """Load chat_folders from user.json."""
    dependencies = chat_route_dependencies()
    settings = dependencies.get_manager().load()
    raw = settings.get("chat_folders") or []
    return raw if isinstance(raw, list) else []


def _save_folders(folders: list[dict]) -> None:
    """Persist chat_folders to user.json (merging with existing keys)."""
    dependencies = chat_route_dependencies()
    dependencies.get_manager().save({"chat_folders": folders})


def _load_profiles(principal: ChatSessionPrincipal | None = None) -> list[dict[str, Any]]:
    """Load built-in and user profiles, with user profiles stored separately."""
    dependencies = chat_route_dependencies()
    with chat_session_mutation_lock:
        manager = dependencies.get_manager()
        settings = manager.load()
        raw_custom = settings.get("chat_profiles") or []
        custom = list(raw_custom) if isinstance(raw_custom, list) else []
        changed = False
        by_id = {str(profile.get("id") or ""): profile for profile in default_chat_profiles()}
        if principal is not None:
            for index, profile in enumerate(custom):
                if not isinstance(profile, dict) or not profile.get("id"):
                    continue
                authorized, migrated_owner = authorize_owned_record(
                    profile,
                    principal,
                    legacy_default_owner=_legacy_chat_owner(),
                )
                changed = changed or migrated_owner
                if not authorized:
                    continue
                migrated_profiles, migrated_settings = _migrate_profile_settings_v3([profile])
                if migrated_profiles:
                    profile = migrated_profiles[0]
                    custom[index] = profile
                changed = changed or migrated_settings
                by_id[str(profile["id"])] = profile
        if changed:
            manager.save({"chat_profiles": custom, "chat_model_version": 3})
        return list(by_id.values())


def _save_custom_profiles(profiles: list[dict[str, Any]]) -> None:
    dependencies = chat_route_dependencies()
    dependencies.get_manager().save({"chat_profiles": profiles})


def _load_chat_types() -> list[dict[str, Any]]:
    dependencies = chat_route_dependencies()
    custom = dependencies.get_manager().load().get("chat_session_types") or []
    by_id = {str(item["id"]): dict(item) for item in DEFAULT_CHAT_TYPES}
    for item in custom if isinstance(custom, list) else []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = dict(item)
    return list(by_id.values())


def _profile_by_id(
    profile_id: str,
    principal: ChatSessionPrincipal | None = None,
) -> dict[str, Any] | None:
    return next(
        (profile for profile in _load_profiles(principal) if str(profile.get("id") or "") == profile_id),
        None,
    )


def _apply_profile(session: dict[str, Any], profile: dict[str, Any]) -> None:
    """Materialize effective profile values while preserving chat overrides."""
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    profile_settings = dict(profile.get("settings") or {})
    effective, _ = resolve_effective_settings(
        _DEFAULT_SESSION_SETTINGS, profile_settings, dict(session.get("settings_delta") or {})
    )
    session["profile_id"] = str(profile.get("id") or "general")
    session["profile_settings"] = profile_settings
    session["profile_system_prompt"] = str(profile.get("system_prompt") or "")
    session["settings"] = effective
    override = str(session.get("system_prompt_override") or "")
    session["system_prompt"] = override or str(profile.get("system_prompt") or "")
