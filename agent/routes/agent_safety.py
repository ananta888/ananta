"""Authenticated Hub API for agent safety control and observability."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.agent_safety_errors import AgentSafetyDenied
from agent.services.agent_safety_state_store import AgentSafetyStateConflictError

agent_safety_bp = Blueprint("agent_safety", __name__, url_prefix="/api/agent-safety")


def _service(name: str = "control"):
    service = current_app.extensions.get(f"agent_safety_{name}_service")
    if service is None:
        raise RuntimeError("agent_safety_unavailable")
    return service


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("agent_safety_payload_invalid")
    return value


def _invoke(operation: Callable[..., dict[str, Any]], payload: dict[str, Any] | None = None):
    try:
        return api_response(data=operation(**(payload if payload is not None else _payload())))
    except AgentSafetyStateConflictError as exc:
        return api_response(status="error", message=str(exc), code=409)
    except AgentSafetyDenied as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@agent_safety_bp.get("/overview")
@check_auth
def overview():
    run_id = str(request.args.get("run_id") or "").strip() or None
    project_id = str(request.args.get("project_id") or "").strip() or None

    def operation(**kwargs: Any) -> dict[str, Any]:
        value = _service().overview(**kwargs)
        wiring = current_app.extensions.get("agent_safety_wiring_status")
        value["containment_available"] = bool(wiring and getattr(wiring, "containment_available", False))
        return value

    return _invoke(operation, {"run_id": run_id, "project_id": project_id})


@agent_safety_bp.post("/policies")
@admin_required
def configure_policy():
    return _invoke(_service().configure_policy)


@agent_safety_bp.post("/runs")
@admin_required
def register_run():
    return _invoke(_service().register_run)


@agent_safety_bp.post("/sentinels")
@admin_required
def issue_sentinel():
    return _invoke(_service().issue_sentinel)


@agent_safety_bp.post("/sentinels/consume")
@check_auth
def consume_sentinel():
    return _invoke(_service().consume_sentinel)


@agent_safety_bp.post("/boundaries")
@check_auth
def record_boundary_event():
    return _invoke(_service().record_boundary_event)


@agent_safety_bp.post("/runs/<run_id>/stop")
@admin_required
def emergency_stop(run_id: str):
    return _invoke(_service().emergency_stop, {**_payload(), "run_id": run_id})


@agent_safety_bp.post("/incidents/<bundle_id>/classify")
@admin_required
def classify_incident(bundle_id: str):
    return _invoke(
        _service("recovery").classify_incident,
        {**_payload(), "bundle_id": bundle_id},
    )


@agent_safety_bp.post("/replays")
@admin_required
def create_replay():
    return _invoke(_service("recovery").create_replay)


@agent_safety_bp.post("/training/records")
@admin_required
def compile_training_records():
    return _invoke(_service("evaluation").compile_training_records)


@agent_safety_bp.post("/training/trigger-series")
@admin_required
def build_trigger_series():
    return _invoke(_service("evaluation").build_trigger_series)


@agent_safety_bp.post("/training/runs")
@admin_required
def submit_training():
    return _invoke(_service("evaluation").submit_training)


@agent_safety_bp.post("/evaluations")
@admin_required
def evaluate_trials():
    return _invoke(_service("evaluation").evaluate_trials)


__all__ = ["agent_safety_bp"]
