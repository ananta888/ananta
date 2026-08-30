"""Authenticated Hub API for optional research-training runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth, check_registered_worker_auth
from agent.common.errors import api_response
from agent.routes.ml_intern_training_route_support import _principal
from agent.services.research_training_run_service import ResearchTrainingDenied
from agent.services.research_training_state_store import ResearchTrainingStateConflict

research_training_bp = Blueprint(
    "research_training", __name__, url_prefix="/api/ml-intern-training/research"
)
_WORKER_SCOPE = "ml-intern:research-training:worker"


def _extension(name: str):
    value = current_app.extensions.get(name)
    if value is None:
        raise RuntimeError("research_training_unavailable")
    return value


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("research_payload_invalid")
    return value


def _invoke(operation: Callable[[], Any], *, created: bool = False):
    try:
        return api_response(data=operation(), code=201 if created else 200)
    except ResearchTrainingStateConflict as exc:
        return api_response(status="error", message=str(exc), code=409)
    except (ResearchTrainingDenied, PermissionError) as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


def _tenant_spec(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("spec")
    if not isinstance(raw, dict):
        raise ValueError("research_spec_required")
    tenant_id = _principal().tenant_id
    if raw.get("tenant_id") not in {None, tenant_id}:
        raise PermissionError("research_tenant_mismatch")
    return {**raw, "tenant_id": tenant_id}


@research_training_bp.get("/capabilities")
@check_auth
@admin_required
def capabilities():
    return _invoke(lambda: _extension("research_training_capabilities").projection())


@research_training_bp.post("/capabilities/worker")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_capabilities():
    def operation():
        service = _extension("research_training_capabilities")
        service.report_worker(_payload())
        return service.projection()

    return _invoke(operation)


@research_training_bp.post("/recipes/resolve")
@check_auth
@admin_required
def resolve_recipe():
    return _invoke(lambda: _extension("research_training_recipes").resolve(_payload()))


@research_training_bp.post("/recipes/sweep")
@check_auth
@admin_required
def resolve_sweep():
    def operation():
        body = _payload()
        return _extension("research_training_recipes").sweep(body.get("request") or {}, body.get("depths") or [])

    return _invoke(operation)


@research_training_bp.post("/dry-run")
@check_auth
@admin_required
def dry_run():
    return _invoke(lambda: _extension("research_training_runs").dry_run(spec=_tenant_spec(_payload())))


@research_training_bp.post("/runs")
@check_auth
@admin_required
def create_run():
    def operation():
        body = _payload()
        key = str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "")
        return _extension("research_training_runs").create(spec=_tenant_spec(body), idempotency_key=key)

    return _invoke(operation, created=True)


@research_training_bp.get("/runs")
@check_auth
@admin_required
def list_runs():
    return _invoke(
        lambda: _extension("research_training_runs").list(
            tenant_id=_principal().tenant_id,
            limit=int(request.args.get("limit") or 100),
        )
    )


@research_training_bp.get("/runs/<run_id>")
@check_auth
@admin_required
def get_run(run_id: str):
    return _invoke(
        lambda: _extension("research_training_runs").get(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
        )
    )


@research_training_bp.post("/runs/<run_id>/cancel")
@check_auth
@admin_required
def cancel_run(run_id: str):
    return _invoke(
        lambda: _extension("research_training_runs").cancel(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            expected_revision=_payload().get("expected_revision"),
        )
    )


@research_training_bp.post("/runs/<run_id>/worker-transition")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_transition(run_id: str):
    return _invoke(lambda: _extension("research_training_runs").transition(**_payload(), run_id=run_id))


@research_training_bp.post("/evaluations")
@check_auth
@admin_required
def evaluate():
    return _invoke(lambda: _extension("research_training_evaluation").compare(**_payload()))


@research_training_bp.post("/release-decision")
@check_auth
@admin_required
def release_decision():
    return _invoke(lambda: _extension("research_training_release_gate").decide(**_payload()))


__all__ = ["research_training_bp"]
