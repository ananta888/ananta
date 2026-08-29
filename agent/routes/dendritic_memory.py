"""Authenticated Hub API for experimental dendritic-memory jobs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth, check_registered_worker_auth
from agent.common.errors import api_response
from agent.routes.ml_intern_training_route_support import _principal
from agent.services.dendritic_memory_job_service import DendriticMemoryDenied
from agent.services.dendritic_memory_registry_service import DendriticMemoryRegistryConflict
from agent.services.dendritic_memory_state_store import DendriticMemoryStateConflict

dendritic_memory_bp = Blueprint(
    "dendritic_memory", __name__, url_prefix="/api/ml-intern-training/dendritic-memory"
)
_WORKER_SCOPE = "ml-intern:dendritic-memory:worker"


def _extension(name: str):
    value = current_app.extensions.get(name)
    if value is None:
        raise RuntimeError("dendritic_memory_unavailable")
    return value


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("dendritic_payload_invalid")
    return value


def _invoke(operation: Callable[[], Any]):
    try:
        return api_response(data=operation())
    except (DendriticMemoryStateConflict, DendriticMemoryRegistryConflict) as exc:
        return api_response(status="error", message=str(exc), code=409)
    except (DendriticMemoryDenied, PermissionError) as exc:
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
        raise ValueError("dendritic_spec_required")
    tenant_id = _principal().tenant_id
    if raw.get("tenant_id") not in {None, tenant_id}:
        raise PermissionError("dendritic_tenant_mismatch")
    return {**raw, "tenant_id": tenant_id}


def _public_job(value: dict[str, Any]) -> dict[str, Any]:
    projected = dict(value)
    projected.pop("worker_authorization", None)
    return projected


@dendritic_memory_bp.get("/capabilities")
@check_auth
@admin_required
def capabilities():
    return _invoke(lambda: _extension("dendritic_memory_capabilities").projection())


@dendritic_memory_bp.post("/capabilities/worker")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_capabilities():
    def operation():
        service = _extension("dendritic_memory_capabilities")
        service.report_worker(_payload())
        return service.projection()

    return _invoke(operation)


@dendritic_memory_bp.post("/dry-run")
@check_auth
@admin_required
def dry_run():
    return _invoke(lambda: _extension("dendritic_memory_jobs").dry_run(spec=_tenant_spec(_payload())))


@dendritic_memory_bp.post("/runs")
@check_auth
@admin_required
def create_run():
    def operation():
        body = _payload()
        key = str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "")
        return _public_job(
            _extension("dendritic_memory_jobs").create(spec=_tenant_spec(body), idempotency_key=key)
        )

    return _invoke(operation)


@dendritic_memory_bp.get("/runs")
@check_auth
@admin_required
def list_runs():
    return _invoke(
        lambda: {
            "items": [
                _public_job(item)
                for item in _extension("dendritic_memory_jobs").list(
                    tenant_id=_principal().tenant_id, limit=int(request.args.get("limit") or 100)
                )["items"]
            ],
            "limit": int(request.args.get("limit") or 100),
        }
    )


@dendritic_memory_bp.get("/runs/<run_id>")
@check_auth
@admin_required
def get_run(run_id: str):
    return _invoke(
        lambda: _public_job(
            _extension("dendritic_memory_jobs").get(tenant_id=_principal().tenant_id, run_id=run_id)
        )
    )


@dendritic_memory_bp.post("/runs/<run_id>/cancel")
@check_auth
@admin_required
def cancel_run(run_id: str):
    return _invoke(
        lambda: _public_job(
            _extension("dendritic_memory_jobs").cancel(
                tenant_id=_principal().tenant_id,
                run_id=run_id,
                expected_revision=_payload().get("expected_revision"),
            )
        )
    )


@dendritic_memory_bp.post("/runs/<run_id>/worker-transition")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_transition(run_id: str):
    return _invoke(
        lambda: _public_job(_extension("dendritic_memory_jobs").transition(**_payload(), run_id=run_id))
    )


@dendritic_memory_bp.post("/evaluations")
@check_auth
@admin_required
def evaluate():
    return _invoke(lambda: _extension("dendritic_memory_evaluation").compare(**_payload()))


@dendritic_memory_bp.get("/packs")
@check_auth
@admin_required
def list_packs():
    return _invoke(
        lambda: _extension("dendritic_memory_registry").list(
            tenant_id=_principal().tenant_id, limit=int(request.args.get("limit") or 100)
        )
    )


@dendritic_memory_bp.get("/packs/<pack_digest>")
@check_auth
@admin_required
def get_pack(pack_digest: str):
    return _invoke(
        lambda: _extension("dendritic_memory_registry").get(
            tenant_id=_principal().tenant_id, pack_digest=pack_digest
        )
    )


@dendritic_memory_bp.post("/packs/<pack_digest>/revoke")
@check_auth
@admin_required
def revoke_pack(pack_digest: str):
    def operation():
        body = _payload()
        return _extension("dendritic_memory_registry").revoke(
            tenant_id=_principal().tenant_id,
            pack_digest=pack_digest,
            expected_revision=body.get("expected_revision"),
            idempotency_key=str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or ""),
            reason_code=str(body.get("reason_code") or "dendritic_pack_revoked_by_policy"),
        )

    return _invoke(operation)


__all__ = ["dendritic_memory_bp"]
