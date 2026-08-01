"""Tenant-bound project lifecycle API used by the Angular control center."""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable, Mapping

from flask import Blueprint, Response, current_app, jsonify, make_response, request

from agent.auth import (
    admin_required,
    check_auth,
    get_authenticated_source_control_principal,
)
from agent.common.errors import api_response
from agent.models.project_models import (
    ProjectCreateCommand,
    ProjectMembershipUpsertCommand,
    ProjectUpdateCommand,
)
from agent.repository import team_repo, task_repo
from agent.routes.control_center_api import _task_item
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectCapability,
)


projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


def _service():
    service = current_app.extensions.get("project_lifecycle_service")
    if service is None:
        raise RuntimeError("project_lifecycle_unavailable")
    return service


def _authority():
    authority = current_app.extensions.get("project_access_authority")
    if authority is None:
        raise RuntimeError("project_access_authority_unavailable")
    return authority


def _principal():
    return get_authenticated_source_control_principal()


def _scope(
    project_id: str,
    capability: ProjectCapability,
    *,
    include_archived: bool = False,
):
    principal = _principal()
    return _authority().require(
        tenant_id=str(principal.tenant_id or ""),
        project_id=str(project_id or "").strip(),
        subject_id=principal.subject_id,
        capability=capability,
        tenant_admin=principal.is_admin,
        include_archived=include_archived,
    )


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping):
        raise ValueError("project_payload_invalid")
    return dict(value)


def _read_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("project_response_invalid")


def _error(reason_code: str, status_code: int) -> Response:
    response = make_response(
        jsonify({"error": {"code": str(reason_code)}}),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _boundary(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except ProjectAccessError as exc:
            return _error(exc.reason_code, exc.public_status)
        except Exception as exc:
            reason_code = str(
                getattr(exc, "reason_code", "")
                or str(exc)
                or "project_internal_error"
            )
            status_code = int(
                getattr(exc, "public_status", 0)
                or getattr(exc, "status_code", 0)
                or (
                    404
                    if reason_code.endswith("_not_found")
                    else 409
                    if "conflict" in reason_code or "already" in reason_code
                    else 422
                    if "mismatch" in reason_code
                    else 400
                    if reason_code.startswith("project_")
                    else 500
                )
            )
            return _error(reason_code, status_code)

    return wrapped


def _include_archived() -> bool:
    if set(request.args) - {"include_archived"}:
        raise ValueError("project_query_fields_invalid")
    value = str(request.args.get("include_archived") or "false").lower()
    if value not in {"true", "false"}:
        raise ValueError("project_include_archived_invalid")
    return value == "true"


def _legacy_team_item(team: Any) -> dict[str, Any]:
    return {
        "id": str(team.id),
        "name": str(team.name),
        "description": getattr(team, "description", None),
        "status": "legacy_unclaimed",
        "is_active": bool(getattr(team, "is_active", True)),
        "origin": "legacy_team",
        "team_id": str(team.id),
        "version": 0,
        "created_at": None,
        "updated_at": None,
        "archived_at": None,
        "root": None,
        "legacy_unclaimed": True,
        "source_control_ready": False,
    }


@projects_bp.get("")
@check_auth
@_boundary
def list_projects():
    principal = _principal()
    include_archived = _include_archived()
    projects = _service().list_projects(
        tenant_id=str(principal.tenant_id or ""),
        subject_id=principal.subject_id,
        tenant_admin=principal.is_admin,
        include_archived=include_archived,
    )
    items = [_read_payload(item) for item in projects]
    if principal.is_admin:
        mapped_team_ids = {
            str(item.get("team_id") or "") for item in items
        }
        items.extend(
            _legacy_team_item(team)
            for team in team_repo.get_all()
            if str(team.id) not in mapped_team_ids
        )
    return api_response(data={"items": items, "count": len(items)})


@projects_bp.post("")
@check_auth
@admin_required
@_boundary
def create_project():
    principal = _principal()
    body = _payload()
    if set(body) - {"name", "description", "team_id"}:
        raise ValueError("project_fields_invalid")
    team_id = str(body.get("team_id") or "").strip() or None
    name = str(body.get("name") or "").strip()
    if team_id and not name:
        team = team_repo.get_by_id(team_id)
        if team is None:
            raise ValueError("project_team_not_found")
        name = str(team.name)
    if not name:
        raise ValueError("project_name_required")
    created = _service().create_project(
        ProjectCreateCommand(
            tenant_id=str(principal.tenant_id or ""),
            name=name,
            owner_subject_id=principal.subject_id,
            description=body.get("description"),
            team_id=team_id,
        )
    )
    response = make_response(
        api_response(data={"project": _read_payload(created)}, code=201)
    )
    response.headers["Location"] = f"/api/projects/{created.id}"
    response.headers["Cache-Control"] = "no-store"
    return response


@projects_bp.get("/<project_id>")
@check_auth
@_boundary
def project_detail(project_id: str):
    project = _service().get_project(
        _scope(project_id, ProjectCapability.READ, include_archived=True)
    )
    return api_response(data={"project": _read_payload(project)})


def _expected_version(body: Mapping[str, Any] | None = None) -> int | None:
    raw = str(request.headers.get("If-Match") or "").strip().strip('"')
    if raw.startswith("project-v1:"):
        raw = raw.split(":", 1)[1]
    if not raw and body is not None:
        raw = str(body.get("version") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("project_version_invalid") from None
    if value < 1:
        raise ValueError("project_version_invalid")
    return value


@projects_bp.patch("/<project_id>")
@check_auth
@_boundary
def update_project(project_id: str):
    body = _payload()
    if set(body) - {"name", "description", "version"}:
        raise ValueError("project_fields_invalid")
    updated = _service().update_project(
        _scope(project_id, ProjectCapability.MANAGE),
        ProjectUpdateCommand(
            name=body.get("name"),
            description=body.get("description"),
            expected_lock_version=_expected_version(body),
        ),
    )
    return api_response(data={"project": _read_payload(updated)})


@projects_bp.post("/<project_id>/archive")
@check_auth
@_boundary
def archive_project(project_id: str):
    body = request.get_json(silent=True)
    if body not in (None, {}) or set(request.args):
        raise ValueError("project_archive_request_invalid")
    archived = _service().archive_project(
        _scope(
            project_id,
            ProjectCapability.ARCHIVE,
            include_archived=True,
        ),
        expected_lock_version=_expected_version(),
    )
    return api_response(data={"project": _read_payload(archived)})


@projects_bp.delete("/<project_id>")
@check_auth
@_boundary
def delete_project(project_id: str):
    if request.get_json(silent=True) not in (None, {}) or set(request.args):
        raise ValueError("project_archive_request_invalid")
    archived = _service().archive_project(
        _scope(
            project_id,
            ProjectCapability.ARCHIVE,
            include_archived=True,
        ),
        expected_lock_version=_expected_version(),
    )
    return api_response(data={"project": _read_payload(archived)})


@projects_bp.get("/<project_id>/members")
@check_auth
@_boundary
def list_project_members(project_id: str):
    members = _service().list_members(
        _scope(project_id, ProjectCapability.MANAGE_MEMBERS)
    )
    items = [_read_payload(member) for member in members]
    return api_response(
        data={"items": items, "count": len(items), "project_id": project_id}
    )


@projects_bp.put("/<project_id>/members/<path:subject_id>")
@check_auth
@_boundary
def upsert_project_member(project_id: str, subject_id: str):
    body = _payload()
    if set(body) - {"role", "version"}:
        raise ValueError("project_member_fields_invalid")
    member = _service().upsert_member(
        _scope(project_id, ProjectCapability.MANAGE_MEMBERS),
        ProjectMembershipUpsertCommand(
            subject_id=subject_id,
            role=str(body.get("role") or ""),
            expected_lock_version=_expected_version(body),
        ),
    )
    return api_response(data={"member": _read_payload(member)})


@projects_bp.delete("/<project_id>/members/<path:subject_id>")
@check_auth
@_boundary
def revoke_project_member(project_id: str, subject_id: str):
    if set(request.args):
        raise ValueError("project_member_query_invalid")
    member = _service().revoke_member(
        _scope(project_id, ProjectCapability.MANAGE_MEMBERS),
        subject_id=subject_id,
        expected_lock_version=_expected_version(),
    )
    return api_response(data={"member": _read_payload(member)})


@projects_bp.get("/<project_id>/tasks")
@check_auth
@_boundary
def list_project_tasks(project_id: str):
    if set(request.args):
        raise ValueError("project_task_query_invalid")
    scope = _scope(project_id, ProjectCapability.READ)
    items = []
    for task in task_repo.get_all():
        task_project = str(getattr(task, "project_id", None) or "")
        task_tenant = str(getattr(task, "tenant_id", None) or "")
        canonical_match = (
            task_project == scope.project_id
            and task_tenant == scope.tenant_id
        )
        legacy_match = (
            not task_project
            and bool(scope.team_id)
            and str(getattr(task, "team_id", None) or "") == scope.team_id
        )
        if canonical_match or legacy_match:
            items.append(_task_item(task))
    return api_response(
        data={
            "items": items,
            "count": len(items),
            "project_id": project_id,
        }
    )


__all__ = ["projects_bp"]
