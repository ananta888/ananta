from __future__ import annotations

import copy
import json
import logging
import re
import time
import uuid
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from agent.services.chat_organization_service import (
    ChatOrganizationService,
    OrganizationError,
    validate_classification,
    validate_folder_parent,
)
from agent.services.chat_process_binding import (
    clone_graph,
    load_graph,
    normalize_process_ref,
    process_ref_from_fields,
    resolve_effective_process,
    runtime_overlay,
    signal_session_gate,
    start_session_process,
)
from agent.services.chat_provider_probe import ChatProviderProbe
from agent.services.chat_setting_catalog import (
    SettingValidationIssue,
    apply_setting_patch,
    canonical_setting_contract,
    canonical_setting_schema,
    resolve_effective_settings,
    validate_setting_delta,
)
from client_surfaces.operator_tui.chat_state import (
    DEFAULT_CHAT_TYPES,
    add_session,
    default_chat_profiles,
    default_conversations,
    delete_session,
    get_session,
    get_sessions,
    make_session,
    set_active_session,
    update_session_settings,
)
from client_surfaces.operator_tui.config.user_config_manager import get_manager

_log = logging.getLogger(__name__)

chat_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")


def _organization_service() -> ChatOrganizationService:
    return ChatOrganizationService(get_manager())


def _organization_error(exc: OrganizationError):
    return jsonify(exc.payload()), exc.status


def _chat_workflow_principal() -> tuple[str, str] | None:
    identity = getattr(g, "user", None) or getattr(g, "auth_payload", None)
    if identity is None:
        return ("test-user", "test-user") if current_app.testing else None
    if isinstance(identity, dict):
        subject = str(identity.get("sub") or identity.get("username") or "").strip()
        tenant_id = str(
            identity.get("tenant_id")
            or identity.get("tenant")
            or identity.get("organization_id")
            or subject
        ).strip()
    else:
        subject = str(getattr(identity, "username", "") or getattr(identity, "id", "")).strip()
        tenant_id = str(
            getattr(identity, "tenant_id", "")
            or getattr(identity, "organization_id", "")
            or subject
        ).strip()
    return (tenant_id, subject) if tenant_id and subject else None


def _load_chat(*, persist_migration: bool = False) -> dict[str, Any]:
    """Build a minimal chat dict from persisted user.json for session operations."""
    manager = get_manager()
    settings = manager.load()
    sessions = settings.get("chat_sessions") or default_conversations()
    active_id = settings.get("chat_active_session_id") or (sessions[0]["id"] if sessions else "")
    chat = {"ai_sessions": sessions, "active_session_id": active_id, "channels": {}, "_preserve_session_list": True}
    get_sessions(chat)
    # The shared TUI migration only knows its built-in profiles. Re-resolve
    # persisted custom profiles at the HTTP persistence boundary so their
    # values cannot be replaced by compatibility defaults on reload.
    profiles_by_id = {str(profile.get("id") or ""): profile for profile in _load_profiles()}
    for session in chat.get("ai_sessions") or []:
        if not isinstance(session, dict):
            continue
        profile = profiles_by_id.get(str(session.get("profile_id") or "general"))
        if profile is not None:
            _apply_profile(session, profile)
    if _migrate_session_settings_v3(chat.get("ai_sessions") or []) and persist_migration:
        manager.save({"chat_sessions": chat["ai_sessions"], "chat_model_version": 3})
    return chat


def _save_chat(chat: dict[str, Any]) -> None:
    """Persist sessions back to user.json."""
    get_manager().save(
        {
            "chat_sessions": chat.get("ai_sessions") or [],
            "chat_active_session_id": chat.get("active_session_id") or "",
            "chat_model_version": 3,
        }
    )


def _load_folders() -> list[dict]:
    """Load chat_folders from user.json."""
    settings = get_manager().load()
    raw = settings.get("chat_folders") or []
    return raw if isinstance(raw, list) else []


def _save_folders(folders: list[dict]) -> None:
    """Persist chat_folders to user.json (merging with existing keys)."""
    get_manager().save({"chat_folders": folders})


def _load_profiles() -> list[dict[str, Any]]:
    """Load built-in and user profiles, with user profiles stored separately."""
    manager = get_manager()
    settings = manager.load()
    custom = settings.get("chat_profiles") or []
    custom = custom if isinstance(custom, list) else []
    custom, changed = _migrate_profile_settings_v3(custom)
    if changed:
        manager.save({"chat_profiles": custom, "chat_model_version": 3})
    by_id = {str(profile.get("id") or ""): profile for profile in default_chat_profiles()}
    for profile in custom:
        if isinstance(profile, dict) and profile.get("id"):
            by_id[str(profile["id"])] = dict(profile)
    return list(by_id.values())


def _save_custom_profiles(profiles: list[dict[str, Any]]) -> None:
    get_manager().save({"chat_profiles": profiles})


def _chat_setting_contract() -> tuple[dict[str, Any], dict[str, list[str]]]:
    return canonical_setting_contract()


def _validated_profile_settings(raw: Any, *, allow_null_reset: bool = False):
    if not isinstance(raw, dict):
        return None, [{"key": "settings", "error_code": "invalid_type", "expected": "object", "received": raw}]
    defaults, options = _chat_setting_contract()
    normalized, issues = validate_setting_delta(
        raw,
        defaults=defaults,
        allowed_keys=defaults,
        options=options,
        allow_null_reset=allow_null_reset,
    )
    if normalized.get("chat_backend_model") == "":
        if allow_null_reset:
            normalized["chat_backend_model"] = None
        else:
            normalized.pop("chat_backend_model", None)
    credential_ref = normalized.get("chat_backend_credential_ref")
    if credential_ref and not re.fullmatch(r"env://[A-Z][A-Z0-9_]{1,127}", str(credential_ref)):
        issues.append(
            SettingValidationIssue(
                "chat_backend_credential_ref", "invalid_credential_reference", "env://VARIABLE_NAME", credential_ref
            )
        )
    return normalized, [issue.as_dict() for issue in issues]


def _provider_setting_issues(settings: dict[str, Any]) -> list[dict[str, Any]]:
    backend = str(settings.get("chat_backend") or "ananta-worker")
    if settings.get("chat_backend_api_base") and backend == "ananta-worker":
        return [
            {
                "key": "chat_backend_api_base",
                "error_code": "setting_not_allowed_for_provider",
                "expected": "external provider backend",
                "received": settings["chat_backend_api_base"],
            }
        ]
    return []


def _redact_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ("env://***" if key.endswith("credential_ref") and value else value) for key, value in settings.items()
    }


def _migrate_profile_settings_v3(profiles: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    allowed, _ = _chat_setting_contract()
    migrated: list[dict[str, Any]] = []
    changed = False
    for raw in profiles:
        if not isinstance(raw, dict):
            changed = True
            continue
        profile = copy.deepcopy(raw)
        settings = dict(profile.get("settings") or {})
        legacy = dict(profile.get("legacy_settings") or {})
        unknown = {key: value for key, value in settings.items() if key not in allowed}
        if unknown:
            legacy.update(unknown)
            profile["settings"] = {key: value for key, value in settings.items() if key in allowed}
            profile["legacy_settings"] = legacy
            changed = True
        migrated.append(profile)
    return migrated, changed


def _migrate_session_settings_v3(sessions: list[Any]) -> bool:
    allowed, _ = _chat_setting_contract()
    changed = False
    for session in sessions:
        if not isinstance(session, dict):
            continue
        delta = dict(session.get("settings_delta") or {})
        unknown = {key: value for key, value in delta.items() if key not in allowed}
        if unknown:
            legacy = dict(session.get("legacy_settings_delta") or {})
            legacy.update(unknown)
            session["legacy_settings_delta"] = legacy
            session["settings_delta"] = {key: value for key, value in delta.items() if key in allowed}
            changed = True
        if "process_ref" not in session:
            session["process_ref"] = None
            changed = True
        if "process_runs" not in session:
            session["process_runs"] = []
            changed = True
    return changed


def _validated_process_ref(raw: Any) -> dict[str, str] | None:
    ref = normalize_process_ref(raw)
    if ref is not None and load_graph(ref["graph_id"], ref["version"]) is None:
        raise LookupError("process_definition_not_found")
    return ref


def _load_chat_types() -> list[dict[str, Any]]:
    custom = get_manager().load().get("chat_session_types") or []
    by_id = {str(item["id"]): dict(item) for item in DEFAULT_CHAT_TYPES}
    for item in custom if isinstance(custom, list) else []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = dict(item)
    return list(by_id.values())


def _profile_by_id(profile_id: str) -> dict[str, Any] | None:
    return next((profile for profile in _load_profiles() if str(profile.get("id") or "") == profile_id), None)


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


# ── Reusable chat profile CRUD ───────────────────────────────────────────────


@chat_bp.route("/settings/schema", methods=["GET"])
def get_chat_setting_schema():
    return jsonify(canonical_setting_schema())


@chat_bp.route("/profiles", methods=["GET"])
def list_chat_profiles():
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    return jsonify(
        [{**profile, "builtin": str(profile.get("id") or "") in builtin_ids} for profile in _load_profiles()]
    )


@chat_bp.post("/profiles/models")
def discover_chat_profile_models():
    body = request.get_json(silent=True) or {}
    result = ChatProviderProbe().probe(body, timeout_seconds=float(body.get("timeout_seconds") or 2.5))
    return jsonify(result.as_dict()), 200 if result.ok else 422


@chat_bp.post("/profiles/test-connection")
def test_chat_profile_connection():
    body = request.get_json(silent=True) or {}
    result = ChatProviderProbe().probe(body, timeout_seconds=float(body.get("timeout_seconds") or 2.5))
    payload = result.as_dict()
    payload["model_status"] = (
        "available" if result.model_found else "unknown" if result.model_found is None else "not_found"
    )
    return jsonify(payload), 200 if result.ok else 422


@chat_bp.route("/profiles", methods=["POST"])
def create_chat_profile():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    profile_id = str(data.get("id") or f"profile-{uuid.uuid4().hex[:12]}").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile_id):
        return jsonify({"error": "valid profile id and name are required"}), 400
    if _profile_by_id(profile_id) is not None:
        return jsonify({"error": f"Profile '{profile_id}' already exists"}), 409
    settings, issues = _validated_profile_settings(data.get("settings") or {})
    issues.extend(_provider_setting_issues(settings or {}))
    if issues:
        return jsonify({"error": "invalid_profile_settings", "issues": issues}), 422
    try:
        process_ref = _validated_process_ref(process_ref_from_fields({**data, **settings}))
    except (ValueError, LookupError) as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    profile = {
        "id": profile_id,
        "name": name,
        "icon": str(data.get("icon") or "🎯"),
        "description": str(data.get("description") or ""),
        "system_prompt": str(data.get("system_prompt") or ""),
        "settings": settings,
        "process_ref": process_ref,
    }
    custom = list((get_manager().load().get("chat_profiles") or []))
    custom.append(profile)
    _save_custom_profiles(custom)
    return jsonify({**profile, "builtin": False}), 201


@chat_bp.route("/profiles/<profile_id>", methods=["PATCH"])
def update_chat_profile(profile_id: str):
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    data = request.get_json(silent=True) or {}
    custom = list((get_manager().load().get("chat_profiles") or []))
    profile = next((p for p in custom if str((p or {}).get("id") or "") == profile_id), None)
    if profile is None:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    for key in ("name", "icon", "description", "system_prompt"):
        if key in data:
            profile[key] = str(data.get(key) or "")
    if any(
        key in data for key in ("process_ref", "process_definition_id", "process_version", "process_version_policy")
    ):
        try:
            profile["process_ref"] = _validated_process_ref(process_ref_from_fields(data))
        except (ValueError, LookupError) as exc:
            return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    if "settings" in data:
        settings_patch, issues = _validated_profile_settings(data["settings"], allow_null_reset=True)
        candidate_settings = apply_setting_patch(dict(profile.get("settings") or {}), settings_patch or {})
        issues.extend(_provider_setting_issues(candidate_settings))
        candidate_process_ref = process_ref_from_fields(candidate_settings)
        if candidate_process_ref:
            try:
                _validated_process_ref(candidate_process_ref)
            except LookupError as exc:
                issues.append(
                    {
                        "key": "process_definition_id",
                        "error_code": str(exc),
                        "expected": "existing process definition/version",
                        "received": candidate_process_ref,
                    }
                )
        if issues:
            return jsonify({"error": "invalid_profile_settings", "issues": issues}), 422
        profile["settings"] = candidate_settings
        if candidate_process_ref:
            profile["process_ref"] = candidate_process_ref
    _save_custom_profiles(custom)
    chat = _load_chat()
    for session in get_sessions(chat):
        if str(session.get("profile_id") or "") == profile_id:
            _apply_profile(session, profile)
    _save_chat(chat)
    return jsonify({**profile, "builtin": False})


@chat_bp.route("/profiles/<profile_id>", methods=["DELETE"])
def delete_chat_profile(profile_id: str):
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    chat = _load_chat()
    if any(str(session.get("profile_id") or "") == profile_id for session in get_sessions(chat)):
        return jsonify({"error": "profile is still used by chats"}), 409
    custom = list((get_manager().load().get("chat_profiles") or []))
    kept = [p for p in custom if str((p or {}).get("id") or "") != profile_id]
    if len(kept) == len(custom):
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    _save_custom_profiles(kept)
    return "", 204


@chat_bp.get("/profiles/<profile_id>/effective")
def get_effective_chat_profile(profile_id: str):
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    profile = _profile_by_id(profile_id)
    if profile is None:
        return jsonify({"error": "profile_not_found"}), 404
    delta = dict(profile.get("settings") or {})
    effective, provenance = resolve_effective_settings(_DEFAULT_SESSION_SETTINGS, delta, {})
    return jsonify(
        {
            "profile_id": profile_id,
            "settings_delta": _redact_settings(delta),
            "effective_settings": _redact_settings(effective),
            "provenance": provenance,
            "system_prompt": str(profile.get("system_prompt") or ""),
            "process_ref": profile.get("process_ref"),
        }
    )


@chat_bp.post("/profiles/effective-preview")
def preview_effective_chat_profile():
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    body = request.get_json(silent=True) or {}
    profile = _profile_by_id(str(body.get("profile_id") or "general"))
    if profile is None:
        return jsonify({"error": "profile_not_found", "error_code": "profile_not_found"}), 404
    profile_delta = {**dict(profile.get("settings") or {}), **dict(body.get("profile_settings") or {})}
    session_delta = dict(body.get("session_settings_delta") or {})
    effective, provenance = resolve_effective_settings(_DEFAULT_SESSION_SETTINGS, profile_delta, session_delta)
    prompt_override = body.get("system_prompt_override")
    return jsonify(
        {
            "profile_id": profile["id"],
            "effective_settings": _redact_settings(effective),
            "values": {
                key: {"value": _redact_settings({key: value})[key], "source": provenance[key]}
                for key, value in effective.items()
            },
            "system_prompt": {
                "value": str(prompt_override if prompt_override is not None else profile.get("system_prompt") or ""),
                "source": "session" if prompt_override is not None else "profile",
            },
        }
    )


# ── Conversation classification types ───────────────────────────────────────


@chat_bp.route("/types", methods=["GET"])
def list_chat_types():
    builtin_ids = {str(item["id"]) for item in DEFAULT_CHAT_TYPES}
    return jsonify([{**item, "builtin": str(item.get("id") or "") in builtin_ids} for item in _load_chat_types()])


@chat_bp.route("/types", methods=["POST"])
def create_chat_type():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    type_id = str(data.get("id") or f"type-{uuid.uuid4().hex[:12]}").strip()
    subtypes = [str(value).strip() for value in list(data.get("subtypes") or []) if str(value).strip()]
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", type_id):
        return jsonify({"error": "valid type id and name are required"}), 400
    if any(str(item.get("id") or "") == type_id for item in _load_chat_types()):
        return jsonify({"error": f"Type '{type_id}' already exists"}), 409
    item = {
        "id": type_id,
        "name": name,
        "icon": str(data.get("icon") or "🎯"),
        "description": str(data.get("description") or ""),
        "subtypes": subtypes,
    }
    custom = list((get_manager().load().get("chat_session_types") or []))
    custom.append(item)
    get_manager().save({"chat_session_types": custom})
    return jsonify({**item, "builtin": False}), 201


@chat_bp.route("/types/<type_id>", methods=["PATCH", "DELETE"])
def mutate_chat_type(type_id: str):
    if type_id in {str(item["id"]) for item in DEFAULT_CHAT_TYPES}:
        return jsonify({"error": "built-in types are read-only"}), 409
    custom = list((get_manager().load().get("chat_session_types") or []))
    item = next((entry for entry in custom if str((entry or {}).get("id") or "") == type_id), None)
    if item is None:
        return jsonify({"error": f"Type '{type_id}' not found"}), 404
    if request.method == "DELETE":
        chat = _load_chat()
        if any(str(session.get("session_type") or "") == type_id for session in get_sessions(chat)):
            return jsonify({"error": "type is still used by chats", "error_code": "type_in_use"}), 409
        get_manager().save({"chat_session_types": [entry for entry in custom if entry is not item]})
        return "", 204
    data = request.get_json(silent=True) or {}
    for key in ("name", "icon", "description"):
        if key in data:
            item[key] = str(data.get(key) or "")
    if "subtypes" in data and isinstance(data["subtypes"], list):
        item["subtypes"] = [str(value).strip() for value in data["subtypes"] if str(value).strip()]
    get_manager().save({"chat_session_types": custom})
    return jsonify({**item, "builtin": False})


@chat_bp.route("/sessions", methods=["GET"])
def list_chat_sessions():
    chat = _load_chat(persist_migration=True)
    sessions = get_sessions(chat)
    _save_chat(chat)  # persist any newly added default sessions / backfilled fields
    return jsonify([s.copy() for s in sessions])


@chat_bp.route("/sessions", methods=["POST"])
def create_chat_session():
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    session_id = data.get("id")
    name = data.get("name")
    if not session_id or not name:
        return jsonify({"error": "Session ID and name are required"}), 400

    chat = _load_chat()
    if get_session(chat, session_id):
        return jsonify({"error": f"Session with ID '{session_id}' already exists"}), 409

    profile_id = str(data.get("profile_id") or "general")
    profile = _profile_by_id(profile_id)
    if profile is None:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 400
    folder_id = str(data.get("folder_id") or "")
    if folder_id and not any(str(item.get("id") or "") == folder_id for item in _load_folders()):
        return jsonify({"error": f"Folder '{folder_id}' not found", "error_code": "folder_not_found"}), 400
    session_type = str(data.get("session_type") or "")
    session_subtype = str(data.get("session_subtype") or "")
    try:
        validate_classification(_load_chat_types(), session_type, session_subtype)
    except OrganizationError as exc:
        return _organization_error(exc)
    new_session = make_session(
        session_id=session_id,
        name=name,
        system_prompt=data.get("system_prompt", ""),
        icon=data.get("icon", "💬"),
        group=data.get("group", ""),
        folder_id=folder_id,
        session_type=session_type,
        session_subtype=session_subtype,
        type_description=data.get("type_description", ""),
        settings=data.get("settings") or {},
        profile_id=profile_id,
    )
    try:
        new_session["process_ref"] = _validated_process_ref(
            process_ref_from_fields({**data, **dict(data.get("settings") or {})})
        )
    except (ValueError, LookupError) as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    _apply_profile(new_session, profile)
    add_session(chat, new_session)
    set_active_session(chat, session_id)
    _save_chat(chat)
    return jsonify(new_session.copy()), 201


@chat_bp.route("/sessions/<session_id>", methods=["GET"])
def get_single_chat_session(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    return jsonify(session.copy())


@chat_bp.route("/sessions/<session_id>", methods=["PUT", "PATCH"])
def update_chat_session(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    next_folder_id = str(data.get("folder_id", session.get("folder_id") or "") or "")
    if next_folder_id and not any(str(item.get("id") or "") == next_folder_id for item in _load_folders()):
        return jsonify({"error": f"Folder '{next_folder_id}' not found", "error_code": "folder_not_found"}), 400
    next_type = str(data.get("session_type", session.get("session_type") or "") or "")
    next_subtype = str(data.get("session_subtype", session.get("session_subtype") or "") or "")
    try:
        validate_classification(_load_chat_types(), next_type, next_subtype)
    except OrganizationError as exc:
        return _organization_error(exc)

    structure_operations: list[dict[str, Any]] = []
    if "name" in data:
        structure_operations.append(
            {
                "operation_id": "rename",
                "type": "conversation.rename",
                "target_id": session_id,
                "after": str(data.get("name") or "").strip(),
            }
        )
    if "folder_id" in data:
        structure_operations.append(
            {
                "operation_id": "move",
                "type": "conversation.move",
                "target_id": session_id,
                "after": next_folder_id,
            }
        )
    if "sort_order" in data:
        structure_operations.append(
            {
                "operation_id": "reorder",
                "type": "conversation.reorder",
                "target_id": session_id,
                "after": int(data.get("sort_order") or 0),
            }
        )
    if structure_operations:
        try:
            _organization_service().apply_manual(f"Conversation '{session_id}' updated", structure_operations)
        except OrganizationError as exc:
            return _organization_error(exc)
        chat = _load_chat()
        session = get_session(chat, session_id)
        if session is None:
            return jsonify({"error": f"Session '{session_id}' not found"}), 404

    if "name" in data and not structure_operations:
        session["name"] = data["name"]
    if "system_prompt" in data:
        session["system_prompt_override"] = str(data["system_prompt"] or "")
    if "icon" in data:
        session["icon"] = data["icon"]
    if "group" in data:
        session["group"] = str(data["group"] or "")
    if "folder_id" in data and not structure_operations:
        session["folder_id"] = str(data["folder_id"] or "")
    if "session_type" in data:
        session["session_type"] = str(data["session_type"] or "")
    if "session_subtype" in data:
        session["session_subtype"] = str(data["session_subtype"] or "")
    if "type_description" in data:
        session["type_description"] = str(data["type_description"] or "")
    if "profile_id" in data:
        profile_id = str(data["profile_id"] or "general")
        profile = _profile_by_id(profile_id)
        if profile is None:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 400
        _apply_profile(session, profile)
    if any(
        key in data for key in ("process_ref", "process_definition_id", "process_version", "process_version_policy")
    ):
        try:
            session["process_ref"] = _validated_process_ref(process_ref_from_fields(data))
        except (ValueError, LookupError) as exc:
            return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    if "settings" in data and isinstance(data["settings"], dict):
        requested_ref = process_ref_from_fields(data["settings"])
        if requested_ref:
            try:
                _validated_process_ref(requested_ref)
            except LookupError as exc:
                return jsonify({"error": str(exc), "error_code": str(exc)}), 422
        update_session_settings(chat, session_id, data["settings"])
        if requested_ref:
            session["process_ref"] = requested_ref

    profile = _profile_by_id(str(session.get("profile_id") or "general"))
    if profile is not None:
        _apply_profile(session, profile)

    _save_chat(chat)
    session = get_session(chat, session_id)
    return jsonify((session or {}).copy())


@chat_bp.get("/sessions/<session_id>/process")
def get_effective_session_process(session_id: str):
    session = get_session(_load_chat(), session_id)
    if session is None:
        return jsonify({"error": "session_not_found"}), 404
    profile = _profile_by_id(str(session.get("profile_id") or "general"))
    result = resolve_effective_process(session, profile)
    if result["process_ref"] and result["graph"] is None:
        return jsonify({**result, "error": "process_graph_not_found"}), 404
    return jsonify(result)


@chat_bp.post("/sessions/<session_id>/process/clone")
def clone_effective_session_process(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": "session_not_found"}), 404
    profile = _profile_by_id(str(session.get("profile_id") or "general"))
    effective = resolve_effective_process(session, profile)
    graph_id = str((effective.get("process_ref") or {}).get("graph_id") or "")
    if not graph_id:
        return jsonify({"error": "process_not_configured"}), 409
    try:
        graph = clone_graph(graph_id, owner_session_id=session_id)
    except LookupError:
        return jsonify({"error": "process_graph_not_found"}), 404
    session["process_ref"] = {"graph_id": graph["id"], "version": str(graph.get("version") or "1.0")}
    _save_chat(chat)
    return jsonify({"process_ref": session["process_ref"], "graph": graph, "source": "session_override"}), 201


@chat_bp.get("/sessions/<session_id>/process/runs")
def list_session_process_runs(session_id: str):
    session = get_session(_load_chat(), session_id)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    runs = sorted(session.get("process_runs") or [], key=lambda item: item.get("started_at", 0), reverse=True)
    summaries = []
    for item in runs:
        summary = {key: value for key, value in item.items() if key != "graph_snapshot"}
        summary["status"] = runtime_overlay(item)["overall_status"]
        summaries.append(summary)
    return jsonify(summaries)


@chat_bp.post("/sessions/<session_id>/process/runs")
def start_session_process_run(session_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    effective = resolve_effective_process(session, _profile_by_id(str(session.get("profile_id") or "general")))
    if effective.get("graph") is None:
        return jsonify({"error": "process_not_configured", "error_code": "process_not_configured"}), 409
    body = request.get_json(silent=True) or {}
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    tenant_id, subject_id = principal
    try:
        run = start_session_process(
            session_id=session_id,
            graph=effective["graph"],
            message_id=str(body.get("message_id") or ""),
            tenant_id=tenant_id,
            subject_id=subject_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    runs = list(session.get("process_runs") or [])
    runs.append(run)
    session["process_runs"] = runs[-20:]
    _save_chat(chat)
    return jsonify(run), 201


@chat_bp.get("/sessions/<session_id>/process/runs/<run_id>")
def get_session_process_run(session_id: str, run_id: str):
    session = get_session(_load_chat(), session_id)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    run = next((item for item in session.get("process_runs") or [] if str(item.get("run_id")) == run_id), None)
    if run is None:
        return jsonify({"error": "process_run_not_found", "error_code": "process_run_not_found"}), 404
    return jsonify(runtime_overlay(run))


@chat_bp.post("/sessions/<session_id>/process/runs/<run_id>/gate")
def signal_session_process_run_gate(session_id: str, run_id: str):
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    run = next((item for item in session.get("process_runs") or [] if str(item.get("run_id")) == run_id), None)
    if run is None:
        return jsonify({"error": "process_run_not_found", "error_code": "process_run_not_found"}), 404
    body = request.get_json(silent=True) or {}
    identity = getattr(g, "user", None) or getattr(g, "auth_payload", None)
    if identity is None and not current_app.testing:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    actor = str(
        getattr(identity, "username", "") or (identity.get("sub") if isinstance(identity, dict) else "") or "test-user"
    )
    idempotency_key = str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return jsonify({"error": "idempotency_key_required", "error_code": "idempotency_key_required"}), 400
    actions = list(session.get("process_gate_actions") or [])
    previous = next((item for item in actions if item.get("idempotency_key") == idempotency_key), None)
    if previous is not None:
        return jsonify({"status": "already_applied", "action": previous}), 200
    try:
        result = signal_session_gate(
            run=run, step_id=str(body.get("step_id") or ""), decision=str(body.get("decision") or ""), actor=actor
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 409
    action = {
        "idempotency_key": idempotency_key,
        "actor": actor,
        "workflow_id": run_id,
        "step_id": str(body.get("step_id") or ""),
        "decision": str(body.get("decision") or ""),
        "created_at": time.time(),
    }
    actions.append(action)
    session["process_gate_actions"] = actions[-100:]
    _save_chat(chat)
    _log.info(
        "chat_process_gate actor=%s workflow_id=%s step_id=%s decision=%s idempotency_key=%s",
        action["actor"],
        run_id,
        action["step_id"],
        action["decision"],
        idempotency_key,
    )
    return jsonify(result)


@chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_chat_session(session_id: str):
    chat = _load_chat()
    if get_session(chat, session_id) is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    if len(get_sessions(chat)) <= 1:
        return jsonify({"error": "Cannot delete the last remaining session"}), 400
    delete_session(chat, session_id)
    _save_chat(chat)
    return "", 204


@chat_bp.route("/sessions/<session_id>/activate", methods=["POST"])
def activate_chat_session(session_id: str):
    chat = _load_chat()
    if get_session(chat, session_id) is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    set_active_session(chat, session_id)
    _save_chat(chat)
    return jsonify({"message": f"Session '{session_id}' activated"}), 200


# ── Folder CRUD ──────────────────────────────────────────────────────────────


@chat_bp.route("/folders", methods=["GET"])
def list_folders():
    return jsonify(_load_folders())


@chat_bp.route("/folders", methods=["POST"])
def create_folder():
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    folder_id = data.get("id") or f"folder-{uuid.uuid4().hex[:12]}"
    folders = _load_folders()
    if any(f.get("id") == folder_id for f in folders):
        return jsonify({"error": f"Folder '{folder_id}' already exists"}), 409
    parent_id = str(data.get("parent_id") or "")
    try:
        validate_folder_parent(folders, folder_id, parent_id)
    except OrganizationError as exc:
        return _organization_error(exc)
    try:
        revision = _organization_service().apply_manual(
            f"Folder '{name}' created",
            [
                {
                    "operation_id": f"create-{folder_id}",
                    "type": "folder.create",
                    "target_id": folder_id,
                    "temp_id": folder_id,
                    "after": {
                        "name": name,
                        "icon": str(data.get("icon") or "📁"),
                        "parent_id": parent_id,
                        "color": str(data.get("color") or ""),
                        "sort_order": int(data.get("sort_order") or 0),
                    },
                }
            ],
        )
    except OrganizationError as exc:
        return _organization_error(exc)
    folder = next(item for item in revision["after_snapshot"]["folders"] if item["id"] == folder_id)
    return jsonify(folder), 201


@chat_bp.route("/folders/<folder_id>", methods=["PATCH"])
def update_folder(folder_id: str):
    folders = _load_folders()
    folder = next((f for f in folders if f.get("id") == folder_id), None)
    if folder is None:
        return jsonify({"error": f"Folder '{folder_id}' not found"}), 404
    data = request.json or {}
    operations: list[dict[str, Any]] = []
    if "name" in data:
        operations.append(
            {
                "operation_id": "rename",
                "type": "folder.rename",
                "target_id": folder_id,
                "after": str(data["name"] or "").strip() or folder["name"],
            }
        )
    if "icon" in data:
        operations.append(
            {
                "operation_id": "icon",
                "type": "folder.update_icon",
                "target_id": folder_id,
                "after": str(data["icon"] or "📁"),
            }
        )
    if "parent_id" in data:
        parent_id = str(data["parent_id"] or "")
        try:
            validate_folder_parent(folders, folder_id, parent_id)
        except OrganizationError as exc:
            return _organization_error(exc)
        operations.append({"operation_id": "move", "type": "folder.move", "target_id": folder_id, "after": parent_id})
    if "color" in data:
        operations.append(
            {
                "operation_id": "color",
                "type": "folder.update_color",
                "target_id": folder_id,
                "after": str(data["color"] or ""),
            }
        )
    if "sort_order" in data:
        operations.append(
            {
                "operation_id": "reorder",
                "type": "folder.reorder",
                "target_id": folder_id,
                "after": int(data["sort_order"] or 0),
            }
        )
    if operations:
        try:
            revision = _organization_service().apply_manual(f"Folder '{folder_id}' updated", operations)
        except OrganizationError as exc:
            return _organization_error(exc)
        folder = next(item for item in revision["after_snapshot"]["folders"] if item["id"] == folder_id)
    return jsonify(folder)


@chat_bp.route("/folders/<folder_id>", methods=["DELETE"])
def delete_folder(folder_id: str):
    folders = _load_folders()
    if not any(f.get("id") == folder_id for f in folders):
        return jsonify({"error": f"Folder '{folder_id}' not found"}), 404
    chat = _load_chat()
    has_children = any(str(item.get("parent_id") or "") == folder_id for item in folders)
    has_sessions = any(str(item.get("folder_id") or "") == folder_id for item in get_sessions(chat))
    if has_children or has_sessions:
        return jsonify({"error": "folder is not empty", "error_code": "folder_not_empty"}), 409
    try:
        _organization_service().apply_manual(
            f"Folder '{folder_id}' deleted",
            [{"operation_id": "delete", "type": "folder.delete_if_empty", "target_id": folder_id}],
        )
    except OrganizationError as exc:
        return _organization_error(exc)
    return "", 204


# ── Organization proposals and revision history ─────────────────────────────


@chat_bp.route("/organization/snapshot", methods=["GET"])
def get_organization_snapshot():
    return jsonify(_organization_service().snapshot())


@chat_bp.route("/organization/proposals", methods=["GET", "POST"])
def organization_proposals():
    service = _organization_service()
    try:
        if request.method == "GET":
            return jsonify(service.list_proposals())
        return jsonify(service.create_proposal(request.get_json(silent=True) or {})), 201
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/proposals/<proposal_id>", methods=["GET", "PATCH", "DELETE"])
def organization_proposal(proposal_id: str):
    service = _organization_service()
    try:
        if request.method == "GET":
            return jsonify(service.get_proposal(proposal_id))
        if request.method == "DELETE":
            service.discard_proposal(proposal_id)
            return "", 204
        return jsonify(service.update_proposal(proposal_id, request.get_json(silent=True) or {}))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/proposals/<proposal_id>/validate", methods=["POST"])
def validate_organization_proposal(proposal_id: str):
    try:
        return jsonify(_organization_service().validate_proposal(proposal_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/proposals/<proposal_id>/apply", methods=["POST"])
def apply_organization_proposal(proposal_id: str):
    try:
        return jsonify(_organization_service().apply_proposal(proposal_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/history", methods=["GET"])
def organization_history():
    return jsonify(_organization_service().list_revisions())


@chat_bp.route("/organization/history/<revision_id>", methods=["GET"])
def organization_revision(revision_id: str):
    try:
        return jsonify(_organization_service().get_revision(revision_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/history/<revision_id>/revert", methods=["POST"])
def revert_organization_revision(revision_id: str):
    try:
        return jsonify(_organization_service().revert_revision(revision_id))
    except OrganizationError as exc:
        return _organization_error(exc)


# ── AI Reorganize ─────────────────────────────────────────────────────────────


def _heuristic_reorganize(sessions: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Group sessions by 'group' (fallback session_type, then 'Allgemein')."""
    group_map: dict[str, list[dict]] = {}
    for s in sessions:
        g = (s.get("group") or s.get("session_type") or "").strip() or "Allgemein"
        group_map.setdefault(g, []).append(s)

    proposed_folders: list[dict] = []
    assignments: dict[str, str] = {}
    for group_name, group_sessions in group_map.items():
        folder_id = f"folder-{uuid.uuid4().hex[:12]}"
        icon = "📁"
        if "arch" in group_name.lower() or "architektur" in group_name.lower():
            icon = "🏗️"
        elif "konfig" in group_name.lower() or "config" in group_name.lower():
            icon = "⚙️"
        elif "schreib" in group_name.lower() or "writing" in group_name.lower():
            icon = "✍️"
        elif "code" in group_name.lower():
            icon = "💻"
        proposed_folders.append(
            {
                "id": folder_id,
                "name": group_name,
                "icon": icon,
                "parent_id": "",
                "color": "",
            }
        )
        for s in group_sessions:
            assignments[s["id"]] = folder_id

    return proposed_folders, assignments


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences (``` / ```json) around an LLM JSON answer."""
    stripped = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```\s*$", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _llm_reorganize(
    sessions: list[dict],
    folders: list[dict],
    *,
    input_policy: str = "metadata_only",
) -> tuple[list[dict], dict[str, str]]:
    """Ask the LLM for a folder proposal. Raises on any failure or invalid output."""
    from agent.services.chat_partial_summary_service import call_llm_text

    folder_by_id = {str(item.get("id") or ""): item for item in folders}

    def folder_path(folder_id: str) -> str:
        names: list[str] = []
        seen: set[str] = set()
        while folder_id and folder_id not in seen:
            seen.add(folder_id)
            folder = folder_by_id.get(folder_id)
            if folder is None:
                break
            names.append(str(folder.get("name") or folder_id))
            folder_id = str(folder.get("parent_id") or "")
        return "/".join(reversed(names))

    session_lines = []
    remaining_context_chars = 12000
    for s in sessions:
        preview = ""
        if input_policy == "metadata_plus_preview":
            preview = str(s.get("last_message_preview") or "").replace("\n", " ")[:160]
        line = (
            f"{s.get('id')} | {s.get('name') or ''} | {s.get('session_type') or ''} | "
            f"{s.get('session_subtype') or ''} | {s.get('profile_id') or ''} | "
            f"{s.get('group') or ''} | {folder_path(str(s.get('folder_id') or ''))} | "
            f"{int(s.get('message_count') or 0)} | {preview}"
        )
        if remaining_context_chars <= 0:
            break
        session_lines.append(line[:remaining_context_chars])
        remaining_context_chars -= len(session_lines[-1]) + 1
    example_sid = str(sessions[0].get("id")) if sessions else "session-1"
    prompt = (
        "Du organisierst Chat-Sessions in Ordner. Hier die Sessions "
        "Profile beschreibt die Arbeitsweise, Type/Subtype das Thema und Folder nur die Organisation. "
        "Bewahre sinnvolle bestehende Struktur und schlage nur notwendige Aenderungen vor.\n"
        "Format: id | name | type | subtype | profile | legacy_group | folder_path | "
        "message_count | begrenzte_preview:\n"
        + "\n".join(session_lines)
        + "\n\nErstelle 2-6 thematische Ordner mit deutschen Namen und passenden Emoji-Icons "
        "und weise jede Session genau einem Ordner zu.\n"
        "Antworte NUR mit striktem JSON in exakt diesem Format, ohne Erklärungen:\n"
        '{"folders": [{"id": "f1", "name": "...", "icon": "📁", "parent_id": ""}], '
        f'"assignments": {{"{example_sid}": "f1"}}}}\n'
        "Verwende in assignments die echten Session-IDs als Schlüssel (jede genau einmal, "
        "zeichengenau kopiert, NICHT übersetzen oder umbenennen) und die Ordner-IDs "
        "(f1, f2, ...) als Werte.\n"
        "Gültige Session-IDs: " + ", ".join(str(s.get("id")) for s in sessions)
    )

    raw = call_llm_text(prompt, timeout=30)
    if not raw:
        raise ValueError("empty LLM response")

    parsed = json.loads(_strip_json_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError("LLM response is not a JSON object")
    raw_folders = parsed.get("folders")
    raw_assignments = parsed.get("assignments")
    if not isinstance(raw_folders, list) or not raw_folders:
        raise ValueError("invalid 'folders' in LLM response")
    if not isinstance(raw_assignments, dict) or not raw_assignments:
        raise ValueError("invalid 'assignments' in LLM response")

    # Build proposed-id → final-uuid-id mapping and validated folder dicts.
    session_ids = {str(s.get("id")) for s in sessions}
    id_map: dict[str, str] = {}
    folders: list[dict] = []
    for f in raw_folders:
        if not isinstance(f, dict):
            raise ValueError("folder entry is not an object")
        proposed_id = str(f.get("id") or "").strip()
        name = str(f.get("name") or "").strip()
        if not proposed_id or not name:
            raise ValueError("folder entry missing id or name")
        if proposed_id in id_map:
            raise ValueError(f"duplicate folder id '{proposed_id}'")
        id_map[proposed_id] = f"folder-{uuid.uuid4().hex[:12]}"
        folders.append(
            {
                "id": id_map[proposed_id],
                "name": name,
                "icon": str(f.get("icon") or "📁"),
                "parent_id": str(f.get("parent_id") or "").strip(),
                "color": "",
            }
        )
    # Remap parent_id references (proposed ids → final ids; unknown refs → root).
    for f in folders:
        f["parent_id"] = id_map.get(f["parent_id"], "")

    assignments: dict[str, str] = {}
    for sid, fid in raw_assignments.items():
        sid = str(sid)
        fid = str(fid)
        if sid not in session_ids:
            raise ValueError(f"assignment references unknown session '{sid}'")
        if fid not in id_map:
            raise ValueError(f"assignment references unknown folder '{fid}'")
        assignments[sid] = id_map[fid]

    return folders, assignments


@chat_bp.route("/sessions/ai-reorganize", methods=["POST"])
def ai_reorganize_sessions():
    """Propose a folder structure based on current sessions (LLM first, heuristic fallback).

    Returns a proposal the user can preview and optionally accept via
    separate PATCH calls. No state is modified by this endpoint.
    """
    chat = _load_chat()
    sessions = get_sessions(chat)
    folders = _load_folders()
    data = request.get_json(silent=True) or {}
    input_policy = str(data.get("input_policy") or "metadata_only")
    if input_policy not in {"metadata_only", "metadata_plus_preview"}:
        return jsonify({"error": "unsupported input policy", "error_code": "input_policy_invalid"}), 400

    method = "heuristic"
    try:
        proposed_folders, assignments = _llm_reorganize(sessions, folders, input_policy=input_policy)
        method = "llm"
    except Exception as exc:  # noqa: BLE001 — any LLM failure falls back
        _log.debug("ai-reorganize LLM proposal failed, using heuristic: %s", exc)
        proposed_folders, assignments = _heuristic_reorganize(sessions)

    label = "KI" if method == "llm" else "Heuristik"
    operations: list[dict[str, Any]] = []
    proposed_ids = {str(folder.get("id") or "") for folder in proposed_folders}
    for folder in proposed_folders:
        folder_id = str(folder.get("id") or "")
        operations.append(
            {
                "operation_id": f"create-{folder_id}",
                "type": "folder.create",
                "temp_id": folder_id,
                "after": {key: folder.get(key) for key in ("name", "icon", "color", "parent_id")},
                "rationale": "Thematische Gruppierung",
            }
        )
    for session_id, folder_id in assignments.items():
        if folder_id in proposed_ids:
            operations.append(
                {
                    "operation_id": f"move-{session_id}",
                    "type": "conversation.move",
                    "target_id": session_id,
                    "after": folder_id,
                    "rationale": "Conversation dem vorgeschlagenen Themenordner zuordnen",
                }
            )
    service = _organization_service()
    proposal = service.create_proposal(
        {
            "source": "ai",
            "method": method,
            "input_policy": input_policy,
            "summary": f"Vorschlag ({label}): {len(proposed_folders)} Ordner für {len(sessions)} Sessions",
            "operations": operations,
        }
    )
    proposal = service.validate_proposal(proposal["id"])
    return jsonify(
        {
            **proposal,
            "folders": proposed_folders,
            "assignments": assignments,
            "method": method,
            "summary": proposal["summary"],
        }
    )


# ── Context overview ──────────────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/context-overview", methods=["GET"])
def get_session_context_overview(session_id: str):
    """Return a breakdown of what goes into the next prompt for a given session."""
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    settings = session.get("settings") or {}
    sp = str(session.get("system_prompt") or "")
    return jsonify(
        {
            "session_id": session_id,
            "system_prompt": {
                "text": sp[:200] + ("…" if len(sp) > 200 else ""),
                "chars": len(sp),
                "enabled": True,
            },
            "history": {
                "enabled": bool(settings.get("chat_use_history", True)),
                "max_turns": int(settings.get("chat_history_turns") or 6),
                "max_chars": int(settings.get("chat_history_chars") or 1800),
            },
            "summary": {
                "enabled": bool(settings.get("chat_use_summary", True)),
                "max_chars": int(settings.get("chat_summary_chars") or 600),
            },
            "rag": {
                "enabled": bool(settings.get("chat_use_codecompass", True)),
                "profile": str(settings.get("chat_retrieval_profile") or "auto"),
                "top_k": int(settings.get("chat_rag_top_k") or 12),
                "max_chars": int(settings.get("chat_context_chars") or 4000),
            },
        }
    )


# ── Partial summary ───────────────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/summarize", methods=["POST"])
def summarize_session_messages(session_id: str):
    """Summarize a user-selected slice of chat messages (LLM, extractive fallback)."""
    from agent.services.chat_partial_summary_service import get_chat_partial_summary_service

    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages must be a non-empty list"}), 400
    if not all(isinstance(m, dict) and str(m.get("text") or "").strip() for m in messages):
        return jsonify({"error": "each message must be an object with a non-empty 'text'"}), 400

    settings = session.get("settings") or {}
    target_chars = data.get("target_chars")
    if target_chars is None:
        target_chars = settings.get("chat_partial_summary_chars") or 800
    try:
        target_chars = int(target_chars)
    except (TypeError, ValueError):
        return jsonify({"error": "target_chars must be an integer"}), 400

    instruction = str(data.get("instruction") or "")

    result = get_chat_partial_summary_service().summarize(
        messages,
        target_chars=target_chars,
        instruction=instruction,
    )
    return jsonify(
        {
            "summary": result.summary,
            "method": result.method,
            "source_count": result.source_count,
            "chars": result.chars,
        }
    )


# ── Prompt-assembly preview ───────────────────────────────────────────────────


@chat_bp.route("/sessions/<session_id>/prompt-preview", methods=["POST"])
def preview_session_prompt(session_id: str):
    """Preview how the next prompt would be assembled from the session settings.

    Pure/read-only: mirrors the real assembly pipeline without modifying state.
    History lives client-side, so it arrives in the request body.
    """
    chat = _load_chat()
    session = get_session(chat, session_id)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    message = str(data.get("message") or "")
    if not message.strip():
        return jsonify({"error": "message is required"}), 400

    settings = session.get("settings") or {}

    # ── system_prompt ────────────────────────────────────────────────────
    system_prompt = str(session.get("system_prompt") or "")

    # ── summary ──────────────────────────────────────────────────────────
    use_summary = bool(settings.get("chat_use_summary", True))
    summary_chars = int(settings.get("chat_summary_chars") or 600)
    raw_summary = str(data.get("summary") or "")
    summary_text = ""
    summary_truncated = False
    if use_summary and raw_summary:
        summary_text = raw_summary[:summary_chars]
        summary_truncated = len(raw_summary) > summary_chars

    # ── history ──────────────────────────────────────────────────────────
    use_history = bool(settings.get("chat_use_history", True))
    history_turns = int(settings.get("chat_history_turns") or 6)
    history_chars = int(settings.get("chat_history_chars") or 1800)
    raw_history = data.get("history") if isinstance(data.get("history"), list) else []
    history_text = ""
    history_truncated = False
    if use_history and raw_history:
        entries = [e for e in raw_history if isinstance(e, dict)]
        kept = entries[-history_turns:] if history_turns > 0 else []
        history_truncated = len(kept) < len(entries)
        joined = "\n".join(f"{str(e.get('sender') or '')}: {str(e.get('text') or '')}" for e in kept)
        if len(joined) > history_chars:
            # Truncate from the front — keep the newest end.
            joined = joined[-history_chars:]
            history_truncated = True
        history_text = joined

    # ── rag placeholder ──────────────────────────────────────────────────
    use_rag = bool(settings.get("chat_use_codecompass", True))
    retrieval_profile = str(settings.get("chat_retrieval_profile") or "auto")
    rag_top_k = int(settings.get("chat_rag_top_k") or 12)
    context_chars = int(settings.get("chat_context_chars") or 4000)
    rag_text = (
        f"(RAG-Kontext wird zur Laufzeit abgerufen — Profil: {retrieval_profile}, "
        f"Top-K: {rag_top_k}, max. {context_chars} Zeichen)"
    )

    sections = [
        {
            "name": "system_prompt",
            "enabled": True,
            "chars": len(system_prompt),
            "truncated": False,
            "text": system_prompt,
        },
        {
            "name": "summary",
            "enabled": use_summary,
            "chars": len(summary_text),
            "truncated": summary_truncated,
            "text": summary_text,
        },
        {
            "name": "history",
            "enabled": use_history,
            "chars": len(history_text),
            "truncated": history_truncated,
            "text": history_text,
        },
        {
            "name": "rag",
            "enabled": use_rag,
            "chars": context_chars if use_rag else 0,
            "truncated": False,
            "text": rag_text,
        },
        {
            "name": "user_message",
            "enabled": True,
            "chars": len(message),
            "truncated": False,
            "text": message,
        },
    ]

    headers = {
        "system_prompt": "## System-Prompt",
        "summary": "## Zusammenfassung",
        "history": "## Verlauf",
        "rag": "## Kontext (RAG)",
        "user_message": "## Neue Nachricht",
    }
    blocks = [f"{headers[s['name']]}\n{s['text']}" for s in sections if s["enabled"] and s["text"]]
    assembled_prompt = "\n\n".join(blocks)

    return jsonify(
        {
            "session_id": session_id,
            "sections": sections,
            "total_chars": len(assembled_prompt),
            "assembled_prompt": assembled_prompt,
        }
    )
