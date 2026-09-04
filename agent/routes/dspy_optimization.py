"""Authenticated Hub API for optimization experiments and policy promotion."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth, check_registered_worker_auth
from agent.common.errors import api_response
from agent.services.dspy_optimization_job_service import DspyOptimizationDenied
from agent.services.dspy_optimization_state_store import DspyOptimizationStateConflict
from agent.services.dspy_promotion_service import DspyPromotionConflict

dspy_optimization_bp = Blueprint("dspy_optimization", __name__, url_prefix="/api/dspy-optimization")
_WORKER_SCOPE = "dspy:optimization:worker"


def _extension(name: str):
    value = current_app.extensions.get(name)
    if value is None:
        raise RuntimeError("dspy_optimization_unavailable")
    return value


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("dspy_payload_invalid")
    return value


def _invoke(operation: Callable[..., Any], payload: dict[str, Any] | None = None):
    try:
        return api_response(data=operation(**(payload if payload is not None else _payload())))
    except (DspyOptimizationStateConflict, DspyPromotionConflict) as exc:
        return api_response(status="error", message=str(exc), code=409)
    except (DspyOptimizationDenied, PermissionError) as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@dspy_optimization_bp.get("/capabilities")
@check_auth
def capabilities():
    return _invoke(lambda: _extension("dspy_engine_capabilities").projection(), {})


@dspy_optimization_bp.get("/observability")
@admin_required
def observability():
    return _invoke(lambda: _extension("dspy_optimization_telemetry").projection(), {})


@dspy_optimization_bp.post("/capabilities/worker")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def report_worker_capabilities():
    def operation():
        service = _extension("dspy_engine_capabilities")
        service.report_worker(_payload())
        return service.projection()

    return _invoke(operation, {})


@dspy_optimization_bp.post("/dry-run")
@admin_required
def dry_run():
    return _invoke(lambda: _extension("dspy_optimization_jobs").dry_run(**_payload()), {})


@dspy_optimization_bp.post("/runs")
@admin_required
def create_run():
    def operation():
        body = _payload()
        key = str(request.headers.get("Idempotency-Key") or body.pop("idempotency_key", "")).strip()
        return _extension("dspy_optimization_jobs").create(**body, idempotency_key=key)

    return _invoke(operation, {})


@dspy_optimization_bp.get("/runs")
@admin_required
def list_runs():
    return _invoke(
        lambda: _extension("dspy_optimization_jobs").list(
            tenant_id=str(request.args.get("tenant_id") or ""), limit=int(request.args.get("limit") or 100)
        ),
        {},
    )


@dspy_optimization_bp.get("/runs/<run_id>")
@admin_required
def get_run(run_id: str):
    return _invoke(
        lambda: _extension("dspy_optimization_jobs").get(
            tenant_id=str(request.args.get("tenant_id") or ""), run_id=run_id
        ),
        {},
    )


@dspy_optimization_bp.post("/runs/<run_id>/cancel")
@admin_required
def cancel_run(run_id: str):
    def operation():
        body = _payload()
        return _extension("dspy_optimization_jobs").cancel(
            tenant_id=body.get("tenant_id"), run_id=run_id, expected_revision=body.get("expected_revision")
        )

    return _invoke(operation, {})


@dspy_optimization_bp.post("/runs/recover")
@admin_required
def recover_runs():
    return _invoke(lambda: _extension("dspy_optimization_jobs").recover(**_payload()), {})


@dspy_optimization_bp.post("/runs/<run_id>/worker-transition")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_transition(run_id: str):
    return _invoke(lambda: _extension("dspy_optimization_jobs").worker_transition(**_payload(), run_id=run_id), {})


@dspy_optimization_bp.post("/evaluations")
@admin_required
def evaluate():
    return _invoke(lambda: _extension("dspy_optimization_evaluation").compare(**_payload()), {})


@dspy_optimization_bp.post("/promotions")
@admin_required
def promote():
    return _invoke(lambda: _extension("dspy_optimization_promotion").promote(**_payload()), {})


@dspy_optimization_bp.post("/promotion-plans")
@admin_required
def promote_plan():
    return _invoke(lambda: _extension("dspy_optimization_promotion").promote_plan(**_payload()), {})


@dspy_optimization_bp.post("/promotions/canary")
@admin_required
def set_canary():
    return _invoke(lambda: _extension("dspy_optimization_promotion").set_canary_percent(**_payload()), {})


@dspy_optimization_bp.post("/promotions/stop")
@admin_required
def stop_canary():
    return _invoke(lambda: _extension("dspy_optimization_promotion").stop_canary(**_payload()), {})


@dspy_optimization_bp.get("/provenance")
@admin_required
def provenance():
    return _invoke(
        lambda: _extension("dspy_optimization_promotion").provenance(
            tenant_id=str(request.args.get("tenant_id") or ""),
            scope_id=str(request.args.get("scope_id") or ""),
        ),
        {},
    )


@dspy_optimization_bp.post("/rollbacks")
@admin_required
def rollback():
    return _invoke(lambda: _extension("dspy_optimization_promotion").rollback(**_payload()), {})


__all__ = ["dspy_optimization_bp"]
