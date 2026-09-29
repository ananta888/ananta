"""Chat session CRUD and activation endpoints."""

from __future__ import annotations

from typing import Any

from flask import (
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.routes.chat_blueprint import chat_bp
from agent.routes.chat_route_access import (
    _chat_workflow_principal,
    _organization_error,
    _organization_service,
    _owned_session,
    _owned_sessions,
    _serialized_chat_mutation,
)
from agent.routes.chat_route_persistence import (
    _apply_profile,
    _load_chat,
    _load_chat_types,
    _load_folders,
    _profile_by_id,
    _save_chat,
)
from agent.routes.chat_route_settings import _validated_process_ref
from agent.services.chat_organization_service import (
    OrganizationError,
    validate_classification,
)
from agent.services.chat_process_binding import process_ref_from_fields
from agent.services.chat_session_security import (
    chat_session_mutation_lock,
    public_session,
)
from client_surfaces.operator_tui.chat_state import (
    add_session,
    delete_session,
    get_session,
    make_session,
    set_active_session,
    update_session_settings,
)


@chat_bp.route("/sessions", methods=["GET"])
@check_user_auth
def list_chat_sessions():
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(persist_migration=True, principal=principal)
        sessions, _ = _owned_sessions(chat, principal)
        # Persist newly added defaults, backfilled fields and deterministic
        # legacy ownership in the same serialized transaction.
        _save_chat(chat, principal=principal)
    return jsonify([public_session(session) for session in sessions])


@chat_bp.route("/sessions", methods=["POST"])
@check_user_auth
@_serialized_chat_mutation
def create_chat_session():
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400

    session_id = data.get("id")
    name = data.get("name")
    if not session_id or not name:
        return jsonify({"error": "Session ID and name are required"}), 400

    chat = _load_chat(principal=principal)
    if get_session(chat, session_id):
        return jsonify({"error": "resource_id_unavailable", "error_code": "resource_id_unavailable"}), 409

    profile_id = str(data.get("profile_id") or "general")
    profile = _profile_by_id(profile_id, principal)
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
            process_ref_from_fields({**data, **dict(data.get("settings") or {})}),
            principal,
        )
    except (ValueError, LookupError) as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    _apply_profile(new_session, profile)
    new_session["owner_principal"] = principal.to_dict()
    add_session(chat, new_session)
    set_active_session(chat, session_id)
    _save_chat(chat, principal=principal)
    return jsonify(public_session(new_session)), 201


@chat_bp.route("/sessions/<session_id>", methods=["GET"])
@check_user_auth
def get_single_chat_session(session_id: str):
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    return jsonify(public_session(session))


@chat_bp.route("/sessions/<session_id>", methods=["PUT", "PATCH"])
@check_user_auth
@_serialized_chat_mutation
def update_chat_session(session_id: str):
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
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
        chat = _load_chat(principal=principal)
        session, _ = _owned_session(chat, session_id, principal)
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
        profile = _profile_by_id(profile_id, principal)
        if profile is None:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 400
        _apply_profile(session, profile)
    if any(
        key in data for key in ("process_ref", "process_definition_id", "process_version", "process_version_policy")
    ):
        try:
            session["process_ref"] = _validated_process_ref(process_ref_from_fields(data), principal)
        except (ValueError, LookupError) as exc:
            return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    if "settings" in data and isinstance(data["settings"], dict):
        requested_ref = process_ref_from_fields(data["settings"])
        if requested_ref:
            try:
                _validated_process_ref(requested_ref, principal)
            except LookupError as exc:
                return jsonify({"error": str(exc), "error_code": str(exc)}), 422
        update_session_settings(chat, session_id, data["settings"])
        if requested_ref:
            session["process_ref"] = requested_ref

    profile = _profile_by_id(str(session.get("profile_id") or "general"), principal)
    if profile is not None:
        _apply_profile(session, profile)

    _save_chat(chat, principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    return jsonify(public_session(session or {}))


@chat_bp.route("/sessions/<session_id>", methods=["DELETE"])
@check_user_auth
@_serialized_chat_mutation
def delete_chat_session(session_id: str):
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    owned, _ = _owned_sessions(chat, principal)
    if len(owned) <= 1:
        return jsonify({"error": "Cannot delete the last remaining session"}), 400
    delete_session(chat, session_id)
    _save_chat(chat, principal=principal)
    return "", 204


@chat_bp.route("/sessions/<session_id>/activate", methods=["POST"])
@check_user_auth
@_serialized_chat_mutation
def activate_chat_session(session_id: str):
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    if session is None:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    set_active_session(chat, session_id)
    _save_chat(chat, principal=principal)
    return jsonify({"message": f"Session '{session_id}' activated"}), 200
