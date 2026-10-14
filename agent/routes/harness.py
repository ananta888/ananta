"""Admin-only, scoped read API for the Harness control plane."""

from flask import Blueprint, current_app, request

from agent.auth import admin_required
from agent.common.errors import api_response
from agent.services.harness_operator_service import get_harness_operator_service

harness_bp = Blueprint("harness", __name__, url_prefix="/api/harness")


def _read(operation):
    try:
        service = current_app.extensions.get("harness_operator") or get_harness_operator_service()
        scope = {key: request.args.get(key, "") for key in ("tenant_id", "project_id")}
        return api_response(data=operation(service, scope))
    except ValueError as exc:
        if str(exc) == "harness_search_unknown":
            return api_response(status="error", message="harness_search_unknown", code=404)
        return api_response(status="error", message="harness_operator_request_invalid", code=422)


@harness_bp.get("/searches")
@admin_required
def list_searches():
    return _read(lambda service, scope: service.list(**scope, limit=int(request.args.get("limit", 100))))


@harness_bp.get("/searches/<session_id>")
@admin_required
def get_search(session_id):
    return _read(lambda service, scope: service.get(**scope, session_id=session_id))


@harness_bp.get("/searches/<session_id>/lineage")
@admin_required
def get_lineage(session_id):
    return _read(lambda service, scope: service.lineage(**scope, session_id=session_id))


@harness_bp.post("/route-preview")
@admin_required
def preview_route():
    """Read-only selection preview, never activation or execution authority."""
    from agent.services.harness_router import get_harness_router
    from ananta_contracts.harness_routing import HarnessRouteRequirements

    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("harness_route_request_invalid")
        values = dict(payload)
        task_id = values.pop("task_id")
        values["authorized_tools"] = tuple(values["authorized_tools"])
        router = current_app.extensions.get("harness_router") or get_harness_router()
        return api_response(data=router.route(HarnessRouteRequirements(**values), task_id=task_id).as_dict())
    except (ValueError, KeyError, TypeError):
        return api_response(status="error", message="harness_route_request_invalid", code=422)


@harness_bp.get("/assets")
@admin_required
def list_evolution_assets():
    from agent.services.evolution_asset_operator import get_evolution_asset_operator

    try:
        service = current_app.extensions.get("evolution_asset_operator") or get_evolution_asset_operator()
        items = service.list(
            tenant_id=request.args.get("tenant_id", ""),
            project_id=request.args.get("project_id", ""),
            limit=int(request.args.get("limit", 100)),
        )
        return api_response(data={"items": items})
    except (ValueError, TypeError):
        return api_response(status="error", message="evolution_asset_request_invalid", code=422)
