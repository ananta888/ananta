"""Versioned Hub API for the business-controlling workbench."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_auth

business_controlling_bp = Blueprint(
    "business_controlling",
    __name__,
    url_prefix="/api/v1/controlling",
)


class BusinessControllingWorkbenchPort(Protocol):
    def status(self, *, tenant_id: str, project_id: str) -> Mapping[str, object]: ...

    def profile_import(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def confirm_mapping(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def start_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        request_payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def list_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
    ) -> tuple[Mapping[str, object], ...]: ...

    def set_disposition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        finding_id: str,
        disposition: str,
        expected_revision: int,
    ) -> Mapping[str, object]: ...

    def export_findings(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
    ) -> Mapping[str, object]: ...


def _service() -> BusinessControllingWorkbenchPort:
    service = current_app.extensions.get("business_controlling_workbench")
    if service is None:
        raise RuntimeError("controlling_workbench_unavailable")
    return service


def _body() -> dict[str, object]:
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping):
        raise ValueError("controlling_request_object_required")
    return dict(value)


def _scope(value: Mapping[str, object]) -> tuple[str, str, str]:
    project_id = str(value.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("controlling_scope_required")
    user = getattr(g, "user", {})
    user = user if isinstance(user, Mapping) else {}
    actor_id = str(user.get("sub") or user.get("user_id") or "hub-api").strip()
    is_admin = bool(getattr(g, "is_admin", False))
    user_tenant = str(user.get("tenant_id") or "").strip()
    user_project = str(user.get("project_id") or "").strip()
    requested_tenant = str(value.get("tenant_id") or "").strip()
    tenant_id = user_tenant
    if requested_tenant:
        if not is_admin and requested_tenant != user_tenant:
            raise PermissionError("controlling_scope_denied")
        tenant_id = requested_tenant
    if not tenant_id:
        resolver = current_app.extensions.get("business_controlling_scope_resolver")
        if callable(resolver):
            tenant_id = str(resolver(project_id=project_id, actor_id=actor_id) or "").strip()
    if not tenant_id:
        raise PermissionError("controlling_scope_unresolved")
    if not is_admin and (
        user_project and user_project != project_id
    ):
        raise PermissionError("controlling_scope_denied")
    return tenant_id, project_id, actor_id


def _error(exc: Exception):
    reason = str(exc) or "controlling_request_failed"
    if not reason.startswith("controlling_"):
        reason = "controlling_request_failed"
    if isinstance(exc, PermissionError):
        status = 403
    elif reason == "controlling_workbench_unavailable":
        status = 503
    elif reason.endswith("_not_found"):
        status = 404
    elif reason.endswith("_revision_conflict"):
        status = 409
    else:
        status = 400
    return jsonify({"ok": False, "reason_code": reason}), status


@business_controlling_bp.get("/status")
@check_auth
def controlling_status():
    try:
        tenant_id, project_id, _ = _scope(request.args)
        return jsonify(
            {
                "ok": True,
                "status": dict(
                    _service().status(
                        tenant_id=tenant_id,
                        project_id=project_id,
                    )
                ),
            }
        )
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.post("/imports/profile")
@check_auth
def profile_controlling_import():
    try:
        body = _body()
        tenant_id, project_id, actor_id = _scope(body)
        profile = _service().profile_import(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=body,
        )
        return jsonify({"ok": True, "profile": dict(profile)}), 201
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.post("/mappings/confirm")
@check_auth
def confirm_controlling_mapping():
    try:
        body = _body()
        tenant_id, project_id, actor_id = _scope(body)
        mapping = _service().confirm_mapping(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=body,
        )
        return jsonify({"ok": True, "mapping": dict(mapping)}), 201
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.post("/runs")
@check_auth
def start_controlling_run():
    try:
        body = _body()
        tenant_id, project_id, actor_id = _scope(body)
        run = _service().start_run(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            request_payload=body,
        )
        return jsonify({"ok": True, "run": dict(run)}), 202
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.get("/findings")
@check_auth
def list_controlling_findings():
    try:
        tenant_id, project_id, _ = _scope(request.args)
        findings = _service().list_findings(
            tenant_id=tenant_id,
            project_id=project_id,
        )
        return jsonify({"ok": True, "findings": [dict(item) for item in findings]})
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.post("/findings/<finding_id>/disposition")
@check_auth
def set_controlling_disposition(finding_id: str):
    try:
        body = _body()
        tenant_id, project_id, actor_id = _scope(body)
        if set(body) != {
            "tenant_id",
            "project_id",
            "disposition",
            "expected_revision",
        }:
            raise ValueError("controlling_disposition_shape_invalid")
        updated = _service().set_disposition(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            finding_id=finding_id,
            disposition=str(body["disposition"]),
            expected_revision=int(body["expected_revision"]),
        )
        return jsonify({"ok": True, "finding": dict(updated)})
    except Exception as exc:
        return _error(exc)


@business_controlling_bp.post("/exports")
@check_auth
def export_controlling_findings():
    try:
        body = _body()
        tenant_id, project_id, actor_id = _scope(body)
        if set(body) != {"tenant_id", "project_id"}:
            raise ValueError("controlling_export_shape_invalid")
        report = _service().export_findings(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
        )
        return jsonify({"ok": True, "report": dict(report)})
    except Exception as exc:
        return _error(exc)


__all__ = ["BusinessControllingWorkbenchPort", "business_controlling_bp"]
