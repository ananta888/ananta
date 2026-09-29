"""Chat profile, setting-schema and chat-type endpoints."""

from __future__ import annotations

import re
import uuid

from flask import (
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.routes.chat_blueprint import chat_bp
from agent.routes.chat_route_access import (
    _chat_workflow_principal,
    _legacy_chat_owner,
    _owned_sessions,
    _require_global_chat_admin,
    _serialized_chat_mutation,
)
from agent.routes.chat_route_dependencies import chat_route_dependencies
from agent.routes.chat_route_persistence import (
    _apply_profile,
    _load_chat,
    _load_chat_types,
    _load_profiles,
    _profile_by_id,
    _save_chat,
    _save_custom_profiles,
)
from agent.routes.chat_route_settings import (
    _provider_setting_issues,
    _redact_settings,
    _validated_process_ref,
    _validated_profile_settings,
)
from agent.services.chat_process_binding import process_ref_from_fields
from agent.services.chat_provider_probe import ChatProviderProbe
from agent.services.chat_session_security import (
    authorize_owned_record,
    public_owned_record,
)
from agent.services.chat_setting_catalog import (
    apply_setting_patch,
    canonical_setting_schema,
    resolve_effective_settings,
)
from client_surfaces.operator_tui.chat_state import (
    DEFAULT_CHAT_TYPES,
    default_chat_profiles,
    get_sessions,
)

# ── Reusable chat profile CRUD ───────────────────────────────────────────────


@chat_bp.route("/settings/schema", methods=["GET"])
def get_chat_setting_schema():
    return jsonify(canonical_setting_schema())


@chat_bp.route("/profiles", methods=["GET"])
@check_user_auth
def list_chat_profiles():
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    return jsonify(
        [
            {**public_owned_record(profile), "builtin": str(profile.get("id") or "") in builtin_ids}
            for profile in _load_profiles(principal)
        ]
    )


@chat_bp.post("/profiles/models")
@check_user_auth
def discover_chat_profile_models():
    body = request.get_json(silent=True) or {}
    result = ChatProviderProbe().probe(body, timeout_seconds=float(body.get("timeout_seconds") or 2.5))
    return jsonify(result.as_dict()), 200 if result.ok else 422


@chat_bp.post("/profiles/test-connection")
@check_user_auth
def test_chat_profile_connection():
    body = request.get_json(silent=True) or {}
    result = ChatProviderProbe().probe(body, timeout_seconds=float(body.get("timeout_seconds") or 2.5))
    payload = result.as_dict()
    payload["model_status"] = (
        "available" if result.model_found else "unknown" if result.model_found is None else "not_found"
    )
    return jsonify(payload), 200 if result.ok else 422


@chat_bp.route("/profiles", methods=["POST"])
@check_user_auth
@_serialized_chat_mutation
def create_chat_profile():
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    profile_id = str(data.get("id") or f"profile-{uuid.uuid4().hex[:12]}").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile_id):
        return jsonify({"error": "valid profile id and name are required"}), 400
    custom = list((dependencies.get_manager().load().get("chat_profiles") or []))
    if profile_id in {str(profile.get("id") or "") for profile in default_chat_profiles()} or any(
        str((profile or {}).get("id") or "") == profile_id for profile in custom
    ):
        return jsonify({"error": "resource_id_unavailable", "error_code": "resource_id_unavailable"}), 409
    settings, issues = _validated_profile_settings(data.get("settings") or {})
    issues.extend(_provider_setting_issues(settings or {}))
    if issues:
        return jsonify({"error": "invalid_profile_settings", "issues": issues}), 422
    try:
        process_ref = _validated_process_ref(process_ref_from_fields({**data, **settings}), principal)
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
        "owner_principal": principal.to_dict(),
    }
    custom.append(profile)
    _save_custom_profiles(custom)
    return jsonify({**public_owned_record(profile), "builtin": False}), 201


@chat_bp.route("/profiles/<profile_id>", methods=["PATCH"])
@check_user_auth
@_serialized_chat_mutation
def update_chat_profile(profile_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    data = request.get_json(silent=True) or {}
    custom = list((dependencies.get_manager().load().get("chat_profiles") or []))
    profile = next((p for p in custom if str((p or {}).get("id") or "") == profile_id), None)
    if profile is None or not authorize_owned_record(
        profile,
        principal,
        legacy_default_owner=_legacy_chat_owner(),
    )[0]:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    for key in ("name", "icon", "description", "system_prompt"):
        if key in data:
            profile[key] = str(data.get(key) or "")
    if any(
        key in data for key in ("process_ref", "process_definition_id", "process_version", "process_version_policy")
    ):
        try:
            profile["process_ref"] = _validated_process_ref(process_ref_from_fields(data), principal)
        except (ValueError, LookupError) as exc:
            return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    if "settings" in data:
        settings_patch, issues = _validated_profile_settings(data["settings"], allow_null_reset=True)
        candidate_settings = apply_setting_patch(dict(profile.get("settings") or {}), settings_patch or {})
        issues.extend(_provider_setting_issues(candidate_settings))
        candidate_process_ref = process_ref_from_fields(candidate_settings)
        if candidate_process_ref:
            try:
                _validated_process_ref(candidate_process_ref, principal)
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
    chat = _load_chat(principal=principal)
    owned_sessions, _ = _owned_sessions(chat, principal)
    for session in owned_sessions:
        if str(session.get("profile_id") or "") == profile_id:
            _apply_profile(session, profile)
    _save_chat(chat, principal=principal)
    return jsonify({**public_owned_record(profile), "builtin": False})


@chat_bp.route("/profiles/<profile_id>", methods=["DELETE"])
@check_user_auth
@_serialized_chat_mutation
def delete_chat_profile(profile_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    builtin_ids = {str(profile.get("id") or "") for profile in default_chat_profiles()}
    if profile_id in builtin_ids:
        return jsonify({"error": "built-in profiles are read-only"}), 409
    chat = _load_chat(principal=principal)
    owned_sessions, _ = _owned_sessions(chat, principal)
    if any(str(session.get("profile_id") or "") == profile_id for session in owned_sessions):
        return jsonify({"error": "profile is still used by chats"}), 409
    custom = list((dependencies.get_manager().load().get("chat_profiles") or []))
    profile = next((p for p in custom if str((p or {}).get("id") or "") == profile_id), None)
    if profile is None or not authorize_owned_record(
        profile,
        principal,
        legacy_default_owner=_legacy_chat_owner(),
    )[0]:
        return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
    kept = [p for p in custom if p is not profile]
    _save_custom_profiles(kept)
    return "", 204


@chat_bp.get("/profiles/<profile_id>/effective")
@check_user_auth
def get_effective_chat_profile(profile_id: str):
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    profile = _profile_by_id(profile_id, principal)
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
@check_user_auth
def preview_effective_chat_profile():
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS

    body = request.get_json(silent=True) or {}
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    profile = _profile_by_id(str(body.get("profile_id") or "general"), principal)
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
@check_user_auth
@_require_global_chat_admin
def list_chat_types():
    builtin_ids = {str(item["id"]) for item in DEFAULT_CHAT_TYPES}
    return jsonify([{**item, "builtin": str(item.get("id") or "") in builtin_ids} for item in _load_chat_types()])


@chat_bp.route("/types", methods=["POST"])
@check_user_auth
@_require_global_chat_admin
@_serialized_chat_mutation
def create_chat_type():
    dependencies = chat_route_dependencies()
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
    custom = list((dependencies.get_manager().load().get("chat_session_types") or []))
    custom.append(item)
    dependencies.get_manager().save({"chat_session_types": custom})
    return jsonify({**item, "builtin": False}), 201


@chat_bp.route("/types/<type_id>", methods=["PATCH", "DELETE"])
@check_user_auth
@_require_global_chat_admin
@_serialized_chat_mutation
def mutate_chat_type(type_id: str):
    dependencies = chat_route_dependencies()
    if type_id in {str(item["id"]) for item in DEFAULT_CHAT_TYPES}:
        return jsonify({"error": "built-in types are read-only"}), 409
    custom = list((dependencies.get_manager().load().get("chat_session_types") or []))
    item = next((entry for entry in custom if str((entry or {}).get("id") or "") == type_id), None)
    if item is None:
        return jsonify({"error": f"Type '{type_id}' not found"}), 404
    if request.method == "DELETE":
        chat = _load_chat()
        if any(str(session.get("session_type") or "") == type_id for session in get_sessions(chat)):
            return jsonify({"error": "type is still used by chats", "error_code": "type_in_use"}), 409
        dependencies.get_manager().save({"chat_session_types": [entry for entry in custom if entry is not item]})
        return "", 204
    data = request.get_json(silent=True) or {}
    for key in ("name", "icon", "description"):
        if key in data:
            item[key] = str(data.get(key) or "")
    if "subtypes" in data and isinstance(data["subtypes"], list):
        item["subtypes"] = [str(value).strip() for value in data["subtypes"] if str(value).strip()]
    dependencies.get_manager().save({"chat_session_types": custom})
    return jsonify({**item, "builtin": False})
