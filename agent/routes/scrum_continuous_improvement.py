"""Hub API for versioned Scrum continuous-improvement control loops."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.scrum_state_store import ScrumStateConflictError

scrum_continuous_improvement_bp = Blueprint("scrum_continuous_improvement", __name__)


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("scrum_command_payload_invalid")
    return value


def _service(name: str) -> Any:
    service = current_app.extensions.get(name)
    if service is None:
        raise RuntimeError("scrum_continuous_improvement_unavailable")
    return service


def _invoke(operation: Callable[..., dict[str, Any]], payload: dict[str, Any] | None = None):
    try:
        return api_response(data=operation(**(payload if payload is not None else _payload())))
    except ScrumStateConflictError as exc:
        return api_response(status="error", message=str(exc), code=409)
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=422)
    except TypeError:
        return api_response(status="error", message="scrum_command_shape_invalid", code=400)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@scrum_continuous_improvement_bp.get("/api/scrum/overview")
@check_auth
def scrum_improvement_overview():
    return _invoke(
        _service("scrum_continuous_improvement_query_service").overview,
        {"scope_id": str(request.args.get("scope_id") or "")},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/baselines")
@admin_required
def create_architecture_baseline():
    return _invoke(_service("scrum_architecture_loop_service").create_baseline)


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/baselines/<revision_id>/activate")
@admin_required
def activate_architecture_baseline(revision_id: str):
    return _invoke(
        _service("scrum_architecture_loop_service").activate_baseline,
        {**_payload(), "revision_id": revision_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/evidence")
@admin_required
def record_architecture_evidence():
    return _invoke(_service("scrum_architecture_loop_service").record_delivery_evidence)


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/debt")
@admin_required
def register_architecture_debt():
    return _invoke(_service("scrum_architecture_loop_service").register_debt)


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/changes")
@admin_required
def propose_architecture_change():
    return _invoke(_service("scrum_architecture_loop_service").propose_change)


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/changes/<proposal_id>/review")
@admin_required
def review_architecture_change(proposal_id: str):
    return _invoke(
        _service("scrum_architecture_loop_service").review_change,
        {**_payload(), "proposal_id": proposal_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/changes/<proposal_id>/materialize")
@admin_required
def materialize_architecture_change(proposal_id: str):
    return _invoke(
        _service("scrum_architecture_loop_service").materialize_accepted_change,
        {**_payload(), "proposal_id": proposal_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/architecture/effects")
@admin_required
def evaluate_architecture_effect():
    return _invoke(_service("scrum_architecture_loop_service").evaluate_revision_effect)


@scrum_continuous_improvement_bp.post("/api/scrum/sprints")
@admin_required
def plan_sprint():
    return _invoke(_service("scrum_sprint_control_service").plan)


@scrum_continuous_improvement_bp.get("/api/scrum/sprints/<sprint_id>")
@check_auth
def get_sprint(sprint_id: str):
    return _invoke(_service("scrum_sprint_control_service").require, {"sprint_id": sprint_id})


@scrum_continuous_improvement_bp.post("/api/scrum/sprints/<sprint_id>/transitions")
@admin_required
def transition_sprint(sprint_id: str):
    return _invoke(
        _service("scrum_sprint_control_service").transition,
        {**_payload(), "sprint_id": sprint_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/sprints/<sprint_id>/snapshots")
@admin_required
def create_sprint_snapshot(sprint_id: str):
    return _invoke(
        _service("scrum_sprint_control_service").snapshot,
        {**_payload(), "sprint_id": sprint_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/sprints/<sprint_id>/controls")
@admin_required
def inspect_sprint(sprint_id: str):
    return _invoke(
        _service("scrum_sprint_control_service").inspect_and_adapt,
        {**_payload(), "sprint_id": sprint_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/sprints/<sprint_id>/backlog-adjustments")
@admin_required
def adjust_sprint_backlog(sprint_id: str):
    return _invoke(
        _service("scrum_sprint_control_service").adjust_backlog,
        {**_payload(), "sprint_id": sprint_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/sprints/<sprint_id>/goal-exceptions")
@admin_required
def apply_sprint_goal_exception(sprint_id: str):
    return _invoke(
        _service("scrum_sprint_control_service").apply_goal_exception,
        {**_payload(), "sprint_id": sprint_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/retrospectives/evidence")
@admin_required
def build_retrospective_evidence():
    return _invoke(_service("scrum_retrospective_service").build_evidence_bundle)


@scrum_continuous_improvement_bp.post("/api/scrum/retrospectives")
@admin_required
def analyze_retrospective():
    return _invoke(_service("scrum_retrospective_service").analyze)


@scrum_continuous_improvement_bp.post("/api/scrum/improvements")
@admin_required
def propose_improvement():
    return _invoke(_service("scrum_retrospective_service").propose_improvement)


@scrum_continuous_improvement_bp.post("/api/scrum/improvements/<proposal_id>/review")
@admin_required
def review_improvement(proposal_id: str):
    return _invoke(
        _service("scrum_retrospective_service").review_improvement,
        {**_payload(), "proposal_id": proposal_id},
    )


@scrum_continuous_improvement_bp.post("/api/scrum/improvement-commitments")
@admin_required
def create_improvement_commitment():
    return _invoke(_service("scrum_retrospective_service").create_commitment)


@scrum_continuous_improvement_bp.post("/api/scrum/improvement-effects")
@admin_required
def evaluate_improvement_commitment():
    return _invoke(_service("scrum_retrospective_service").evaluate_commitment)


__all__ = ["scrum_continuous_improvement_bp"]
