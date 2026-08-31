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
    projected.pop("tenant_scope_digest", None)
    return projected


def _idempotency_key(body: dict[str, Any]) -> str:
    return str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "")


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


@dendritic_memory_bp.post("/runs/claim")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def claim_run():
    def operation():
        body = _payload()
        if set(body) - {"limit"}:
            raise ValueError("dendritic_worker_claim_payload_invalid")
        return _extension("dendritic_memory_jobs").claim_next(limit=int(body.get("limit") or 100))

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


@dendritic_memory_bp.post("/runs/<run_id>/resume")
@check_auth
@admin_required
def resume_run(run_id: str):
    def operation():
        body = _payload()
        checkpoint = body.get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise ValueError("dendritic_checkpoint_required")
        return _public_job(
            _extension("dendritic_memory_jobs").resume(
                tenant_id=_principal().tenant_id,
                run_id=run_id,
                expected_revision=body.get("expected_revision"),
                checkpoint=checkpoint,
            )
        )

    return _invoke(operation)


@dendritic_memory_bp.post("/runs/<run_id>/worker-transition")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_transition(run_id: str):
    return _invoke(
        lambda: _public_job(_extension("dendritic_memory_jobs").transition(**_payload(), run_id=run_id))
    )


@dendritic_memory_bp.post("/runs/<run_id>/worker-assignment")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_assignment(run_id: str):
    return _invoke(
        lambda: _extension("dendritic_memory_jobs").worker_assignment(
            tenant_id=str(_payload().get("tenant_id") or ""),
            run_id=run_id,
        )
    )


@dendritic_memory_bp.post("/runs/reconcile")
@check_auth
@admin_required
def reconcile_runs():
    def operation():
        body = _payload()
        allowed = {"stale_after_seconds", "limit"}
        if set(body) - allowed:
            raise ValueError("dendritic_reconcile_payload_invalid")
        return _extension("dendritic_memory_jobs").reconcile(
            stale_after_seconds=int(body.get("stale_after_seconds") or 300),
            limit=int(body.get("limit") or 1000),
        )

    return _invoke(operation)


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


@dendritic_memory_bp.post("/packs")
@check_auth
@admin_required
def quarantine_pack():
    def operation():
        body = _payload()
        manifest = body.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("dendritic_pack_manifest_required")
        tenant_id = _principal().tenant_id
        if manifest.get("tenant_id") not in {None, tenant_id}:
            raise PermissionError("dendritic_tenant_mismatch")
        return _extension("dendritic_memory_registry").quarantine(
            manifest={**manifest, "tenant_id": tenant_id},
            artifact_ref=str(body.get("artifact_ref") or ""),
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


@dendritic_memory_bp.post("/packs/compose")
@check_auth
@admin_required
def compose_packs():
    def operation():
        body = _payload()
        manifest = body.get("output_manifest")
        parents = body.get("parent_pack_digests")
        if not isinstance(manifest, dict) or not isinstance(parents, list):
            raise ValueError("dendritic_composition_payload_invalid")
        tenant_id = _principal().tenant_id
        return _extension("dendritic_memory_registry").compose(
            tenant_id=tenant_id,
            parent_pack_digests=parents,
            output_manifest={**manifest, "tenant_id": tenant_id},
            artifact_ref=str(body.get("artifact_ref") or ""),
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


@dendritic_memory_bp.get("/packs/<pack_digest>")
@check_auth
@admin_required
def get_pack(pack_digest: str):
    return _invoke(
        lambda: _extension("dendritic_memory_registry").get(
            tenant_id=_principal().tenant_id, pack_digest=pack_digest
        )
    )


@dendritic_memory_bp.post("/packs/<pack_digest>/approve")
@check_auth
@admin_required
def approve_pack(pack_digest: str):
    def operation():
        body = _payload()
        evaluation = body.get("evaluation")
        if not isinstance(evaluation, dict):
            raise ValueError("dendritic_evaluation_required")
        return _extension("dendritic_memory_registry").approve_evaluated(
            tenant_id=_principal().tenant_id,
            pack_digest=pack_digest,
            evaluation=evaluation,
            expected_revision=body.get("expected_revision"),
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


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
            idempotency_key=_idempotency_key(body),
            reason_code=str(body.get("reason_code") or "dendritic_pack_revoked_by_policy"),
        )

    return _invoke(operation)


@dendritic_memory_bp.post("/packs/<pack_digest>/reject")
@check_auth
@admin_required
def reject_pack(pack_digest: str):
    def operation():
        body = _payload()
        return _extension("dendritic_memory_registry").reject(
            tenant_id=_principal().tenant_id,
            pack_digest=pack_digest,
            expected_revision=body.get("expected_revision"),
            idempotency_key=_idempotency_key(body),
            reason_code=str(body.get("reason_code") or "dendritic_pack_rejected_by_policy"),
        )

    return _invoke(operation)


@dendritic_memory_bp.delete("/packs/<pack_digest>")
@check_auth
@admin_required
def delete_pack(pack_digest: str):
    def operation():
        body = _payload()
        return _extension("dendritic_memory_lifecycle").delete(
            tenant_id=_principal().tenant_id,
            pack_digest=pack_digest,
            expected_revision=body.get("expected_revision"),
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


@dendritic_memory_bp.get("/audit")
@check_auth
@admin_required
def list_audit():
    return _invoke(
        lambda: _extension("dendritic_memory_registry").audit(
            tenant_id=_principal().tenant_id,
            limit=int(request.args.get("limit") or 100),
        )
    )


@dendritic_memory_bp.get("/runtime/routes")
@check_auth
@admin_required
def list_runtime_routes():
    return _invoke(
        lambda: _extension("dendritic_memory_registry").list_routes(
            tenant_id=_principal().tenant_id,
            limit=int(request.args.get("limit") or 100),
        )
    )


@dendritic_memory_bp.post("/runtime/routes/<scope_id>/activate")
@check_auth
@admin_required
def activate_runtime_route(scope_id: str):
    def operation():
        body = _payload()
        receipt = body.get("gate_receipt")
        if not isinstance(receipt, dict):
            raise ValueError("dendritic_runtime_gate_receipt_required")
        return _extension("dendritic_memory_registry").activate(
            tenant_id=_principal().tenant_id,
            scope_id=scope_id,
            pack_digest=str(body.get("pack_digest") or ""),
            expected_route_revision=body.get("expected_route_revision"),
            gate_receipt=receipt,
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


@dendritic_memory_bp.post("/runtime/routes/<scope_id>/deactivate")
@check_auth
@admin_required
def deactivate_runtime_route(scope_id: str):
    def operation():
        body = _payload()
        return _extension("dendritic_memory_registry").deactivate(
            tenant_id=_principal().tenant_id,
            scope_id=scope_id,
            expected_route_revision=body.get("expected_route_revision"),
            idempotency_key=_idempotency_key(body),
        )

    return _invoke(operation)


__all__ = ["dendritic_memory_bp"]
