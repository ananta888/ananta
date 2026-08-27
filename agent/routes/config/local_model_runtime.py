"""Admin/read-only HTTP surface for Hub-owned local runtime state."""

from __future__ import annotations

import re

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.local_model_runtime_composition import (
    LocalModelRuntimeComposition,
    get_local_model_runtime_composition,
)

local_model_runtime_bp = Blueprint("config_local_model_runtime", __name__)

_REQUEST_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


def _composition() -> LocalModelRuntimeComposition:
    injected = current_app.extensions.get("local_model_runtime_composition")
    if isinstance(injected, LocalModelRuntimeComposition):
        return injected
    if not bool(
        getattr(
            current_app.extensions.get("local_model_runtime_wiring_status"),
            "ready",
            False,
        )
    ):
        raise RuntimeError("local_runtime_disabled")
    return get_local_model_runtime_composition(current_app)


def _hub_required():
    if str(current_app.config.get("ROLE") or "").strip().lower() != "hub":
        return api_response(
            status="error",
            message="local_runtime_hub_only",
            data={"reason_code": "local_runtime_hub_only"},
            code=409,
        )
    return None


@local_model_runtime_bp.get("/models/local-runtime/v1/status")
@check_auth
def get_local_model_runtime_status():
    denied = _hub_required()
    if denied is not None:
        return denied
    try:
        snapshot = _composition().snapshot()
    except (RuntimeError, ValueError):
        return api_response(
            status="error",
            message="local_runtime_status_unavailable",
            data={"reason_code": "local_runtime_status_unavailable"},
            code=503,
        )
    return api_response(data=snapshot.to_wire())


@local_model_runtime_bp.get("/models/local-runtime/v1/invocations")
@check_auth
def get_local_model_runtime_invocations():
    denied = _hub_required()
    if denied is not None:
        return denied
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        return api_response(status="error", message="local_runtime_limit_invalid", code=400)
    if limit < 1 or limit > 500:
        return api_response(status="error", message="local_runtime_limit_invalid", code=400)
    try:
        observer = _composition().invocations
    except RuntimeError:
        return api_response(
            status="error",
            message="local_runtime_status_unavailable",
            data={"reason_code": "local_runtime_status_unavailable"},
            code=503,
        )
    if observer is None:
        return api_response(data={"items": []})
    return api_response(data={"items": [item.to_wire() for item in observer.read(limit=limit)]})


@local_model_runtime_bp.post("/models/local-runtime/v1/decisions")
@admin_required
def evaluate_local_model_runtime_activation():
    denied = _hub_required()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"request_id"}:
        return api_response(status="error", message="local_runtime_request_invalid", code=400)
    request_id = str(body.get("request_id") or "").strip().lower()
    if _REQUEST_ID.fullmatch(request_id) is None:
        return api_response(status="error", message="local_runtime_request_invalid", code=400)
    try:
        decision = _composition().lifecycle.evaluate(
            request_id=request_id,
            capabilities=_composition().capabilities,
        )
    except RuntimeError:
        return api_response(status="error", message="local_runtime_resources_unavailable", code=503)
    return api_response(data=decision.to_wire(), code=200 if decision.admitted else 409)


@local_model_runtime_bp.post("/models/local-runtime/v1/decisions/<decision_id>/apply")
@admin_required
def apply_local_model_runtime_activation(decision_id: str):
    denied = _hub_required()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not set(body) <= {"action"}:
        return api_response(status="error", message="local_runtime_control_invalid", code=400)
    try:
        receipt = _composition().lifecycle.apply(
            decision_id=decision_id,
            action=str(body.get("action") or "activate"),
        )
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=409)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)
    return api_response(data=receipt.model_dump(mode="json", by_alias=True))


__all__ = ["local_model_runtime_bp"]
