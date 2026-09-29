"""Chat folder and organization proposal/history endpoints (global chat admin)."""

from __future__ import annotations

import uuid
from typing import Any

from flask import (
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.routes.chat_blueprint import chat_bp
from agent.routes.chat_route_access import (
    _organization_error,
    _organization_service,
    _require_global_chat_admin,
    _serialized_chat_mutation,
)
from agent.routes.chat_route_persistence import (
    _load_chat,
    _load_folders,
)
from agent.services.chat_organization_service import (
    OrganizationError,
    validate_folder_parent,
)
from client_surfaces.operator_tui.chat_state import get_sessions

# ── Folder CRUD ──────────────────────────────────────────────────────────────


@chat_bp.route("/folders", methods=["GET"])
@check_user_auth
@_require_global_chat_admin
def list_folders():
    return jsonify(_load_folders())


@chat_bp.route("/folders", methods=["POST"])
@check_user_auth
@_require_global_chat_admin
@_serialized_chat_mutation
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
@check_user_auth
@_require_global_chat_admin
@_serialized_chat_mutation
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
@check_user_auth
@_require_global_chat_admin
@_serialized_chat_mutation
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
@check_user_auth
@_require_global_chat_admin
def get_organization_snapshot():
    return jsonify(_organization_service().snapshot())


@chat_bp.route("/organization/proposals", methods=["GET", "POST"])
@check_user_auth
@_require_global_chat_admin
def organization_proposals():
    service = _organization_service()
    try:
        if request.method == "GET":
            return jsonify(service.list_proposals())
        return jsonify(service.create_proposal(request.get_json(silent=True) or {})), 201
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/proposals/<proposal_id>", methods=["GET", "PATCH", "DELETE"])
@check_user_auth
@_require_global_chat_admin
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
@check_user_auth
@_require_global_chat_admin
def validate_organization_proposal(proposal_id: str):
    try:
        return jsonify(_organization_service().validate_proposal(proposal_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/proposals/<proposal_id>/apply", methods=["POST"])
@check_user_auth
@_require_global_chat_admin
def apply_organization_proposal(proposal_id: str):
    try:
        return jsonify(_organization_service().apply_proposal(proposal_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/history", methods=["GET"])
@check_user_auth
@_require_global_chat_admin
def organization_history():
    return jsonify(_organization_service().list_revisions())


@chat_bp.route("/organization/history/<revision_id>", methods=["GET"])
@check_user_auth
@_require_global_chat_admin
def organization_revision(revision_id: str):
    try:
        return jsonify(_organization_service().get_revision(revision_id))
    except OrganizationError as exc:
        return _organization_error(exc)


@chat_bp.route("/organization/history/<revision_id>/revert", methods=["POST"])
@check_user_auth
@_require_global_chat_admin
def revert_organization_revision(revision_id: str):
    try:
        return jsonify(_organization_service().revert_revision(revision_id))
    except OrganizationError as exc:
        return _organization_error(exc)
