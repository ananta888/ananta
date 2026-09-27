"""Admin API for access roles and bindings (WCRB-003).

Every change is audited and becomes a revision; ``/access/revisions/<n>/rollback``
restores any earlier state. ``/access/me`` shows the caller their own roles
and effective grants.
"""

from __future__ import annotations

from flask import Blueprint, request

from agent.auth import admin_required, check_auth, get_current_principal
from agent.common.errors import api_response
from agent.services.access_role_admin_service import get_access_role_admin_service
from agent.services.access_roles import BUILTIN_ROLES, SUBJECT_KINDS, AccessRoleError

access_roles_bp = Blueprint("access_roles", __name__)


def _actor() -> str:
    try:
        return get_current_principal().subject_id or "unknown"
    except Exception:  # noqa: BLE001 -- the actor label must never block the change itself
        return "unknown"


def _error(error: AccessRoleError):
    code = 404 if str(error).endswith("_not_found") else 409 if str(error) in {"binding_exists",
                                                                              "builtin_role_is_read_only"} else 400
    return api_response(status="error", message=str(error), code=code)


@access_roles_bp.route("/access/me", methods=["GET"])
@check_auth
def access_me():
    return api_response(data=get_current_principal().as_dict())


@access_roles_bp.route("/access/roles", methods=["GET"])
@admin_required
def list_access_roles():
    snapshot = get_access_role_admin_service().snapshot()
    return api_response(data={"roles": snapshot["roles"], "builtin": sorted(BUILTIN_ROLES),
                              "subject_kinds": list(SUBJECT_KINDS)})


@access_roles_bp.route("/access/roles/<role_id>", methods=["PUT"])
@admin_required
def save_access_role(role_id: str):
    body = request.get_json(silent=True) or {}
    try:
        revision = get_access_role_admin_service().save_role(
            role_id, name=body.get("name") or role_id, description=body.get("description") or "",
            grants=body.get("grants") or {}, actor=_actor())
    except AccessRoleError as error:
        return _error(error)
    return api_response(data={"role_id": role_id.lower(), "revision": revision})


@access_roles_bp.route("/access/roles/<role_id>", methods=["DELETE"])
@admin_required
def delete_access_role(role_id: str):
    try:
        revision = get_access_role_admin_service().delete_role(role_id.lower(), actor=_actor())
    except AccessRoleError as error:
        return _error(error)
    return api_response(data={"revision": revision})


@access_roles_bp.route("/access/bindings", methods=["GET"])
@admin_required
def list_access_bindings():
    return api_response(data={"bindings": get_access_role_admin_service().snapshot()["bindings"]})


@access_roles_bp.route("/access/bindings", methods=["POST"])
@admin_required
def add_access_binding():
    body = request.get_json(silent=True) or {}
    try:
        binding_id, revision = get_access_role_admin_service().add_binding(
            body.get("subject_kind"), body.get("subject"), body.get("role_id"), actor=_actor(),
            tenant_id=body.get("tenant_id"), project_id=body.get("project_id"))
    except AccessRoleError as error:
        return _error(error)
    return api_response(data={"binding_id": binding_id, "revision": revision}, code=201)


@access_roles_bp.route("/access/bindings/<binding_id>", methods=["DELETE"])
@admin_required
def delete_access_binding(binding_id: str):
    try:
        revision = get_access_role_admin_service().delete_binding(binding_id, actor=_actor())
    except AccessRoleError as error:
        return _error(error)
    return api_response(data={"revision": revision})


@access_roles_bp.route("/access/revisions", methods=["GET"])
@admin_required
def list_access_revisions():
    limit = max(1, min(int(request.args.get("limit") or 50), 200))
    return api_response(data={"revisions": get_access_role_admin_service().revisions(limit)})


@access_roles_bp.route("/access/revisions/<int:revision>/rollback", methods=["POST"])
@admin_required
def rollback_access_revision(revision: int):
    try:
        new_revision = get_access_role_admin_service().rollback(revision, actor=_actor())
    except AccessRoleError as error:
        return _error(error)
    return api_response(data={"restored": revision, "revision": new_revision})
