"""Authenticated Hub API for optional research-training runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth, check_registered_worker_auth
from agent.common.errors import api_response
from agent.routes.ml_intern_training_route_support import _principal
from agent.services.research_training_run_service import ResearchTrainingDenied
from agent.services.research_training_state_store import ResearchTrainingStateConflict

research_training_bp = Blueprint(
    "research_training", __name__, url_prefix="/api/ml-intern-training/research"
)
_WORKER_SCOPE = "ml-intern:research-training:worker"
_API_PREFIX = "/api/ml-intern-training/research"


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


def _project_id() -> str:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    return str(identity.get("project_id") or identity.get("project") or _principal().tenant_id).strip()


def _authenticated_worker_id() -> str:
    identity = dict(
        getattr(g, "worker_identity", {})
        or getattr(g, "service_identity", {})
        or getattr(g, "auth_payload", {})
        or {}
    )
    value = str(identity.get("worker_id") or identity.get("sub") or identity.get("client_id") or "").strip()
    if not value:
        raise PermissionError("research_worker_identity_missing")
    return value


def _worker_assignment(assignment_id: object) -> dict[str, Any]:
    return _extension("research_training_assignments").resolve_for_worker(
        assignment_id=str(assignment_id or ""),
        worker_id=_authenticated_worker_id(),
    )["assignment"]


@research_training_bp.get("/capabilities")
@check_auth
@admin_required
def capabilities():
    return _invoke(lambda: _extension("research_training_capabilities").projection())


@research_training_bp.get("/openapi")
@check_auth
@admin_required
def research_openapi():
    paths: dict[str, dict[str, Any]] = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.rule.startswith(_API_PREFIX):
            continue
        path = rule.rule.replace("<run_id>", "{run_id}")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            paths.setdefault(path, {})[method.lower()] = {
                "operationId": f"research_{rule.endpoint.replace('.', '_')}_{method.lower()}",
                "responses": {
                    "200": {"description": "Bounded Hub response"},
                    "422": {"description": "Closed contract rejected"},
                },
                "security": [{"bearerAuth": []}],
            }
    return _invoke(
        lambda: {
            "openapi": "3.1.0",
            "info": {"title": "Ananta Research Training", "version": "1.0.0"},
            "paths": dict(sorted(paths.items())),
            "components": {
                "securitySchemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer"},
                }
            },
        }
    )


@research_training_bp.post("/capabilities/worker")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_capabilities():
    def operation():
        service = _extension("research_training_capabilities")
        service.report_worker(_payload())
        return service.projection()

    return _invoke(operation)


@research_training_bp.post("/workers/inventory")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_inventory():
    def operation():
        body = _payload()
        if set(body) & {"tenant_id", "run_id", "worker_id"}:
            raise PermissionError("research_worker_authority_override_forbidden")
        worker_id = _authenticated_worker_id()
        if body.get("worker_id") not in {None, worker_id}:
            raise PermissionError("research_worker_identity_mismatch")
        report = _extension("research_training_worker_registry").report({**body, "worker_id": worker_id})
        _extension("research_training_capabilities").report_worker(
            {
                "state": report["state"],
                "reason_code": None,
                "engine_version": next(iter(report["backend_versions"].values())),
                "capabilities": report["capabilities"],
                "gpu_profiles": [report["compute_capability"]],
                "network_probe_performed": False,
            }
        )
        return report

    return _invoke(operation)


@research_training_bp.post("/datasets/admit")
@check_auth
@admin_required
def admit_dataset():
    def operation():
        body = _payload()
        return _extension("research_training_datasets").admit(
            tenant_id=_principal().tenant_id,
            project_id=_project_id(),
            candidates=body.get("candidates") or [],
            policy=body.get("policy") or {},
            evidence_scope=str(body.get("evidence_scope") or "local"),
            synthetic=bool(body.get("synthetic", False)),
        )

    return _invoke(operation, created=True)


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


@research_training_bp.post("/recipes/resolve-explicit")
@check_auth
@admin_required
def resolve_explicit_recipe():
    return _invoke(lambda: _extension("research_training_recipes").resolve_explicit(_payload()))


@research_training_bp.post("/sweeps/plan")
@check_auth
@admin_required
def plan_sweep():
    return _invoke(lambda: _extension("research_training_sweeps").plan(**_payload()))


@research_training_bp.post("/sweeps/materialize")
@check_auth
@admin_required
def materialize_sweep():
    return _invoke(lambda: _extension("research_training_sweeps").materialize(**_payload()), created=True)


@research_training_bp.post("/sweeps/compare")
@check_auth
@admin_required
def compare_sweep():
    return _invoke(lambda: _extension("research_training_sweeps").compare(**_payload()), created=True)


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


@research_training_bp.post("/runs/<run_id>/pause")
@check_auth
@admin_required
def pause_run(run_id: str):
    return _invoke(
        lambda: _extension("research_training_runs").pause(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            expected_revision=_payload().get("expected_revision"),
        )
    )


@research_training_bp.post("/runs/<run_id>/resume")
@check_auth
@admin_required
def resume_run(run_id: str):
    return _invoke(
        lambda: _extension("research_training_runs").resume(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            expected_revision=_payload().get("expected_revision"),
        )
    )


@research_training_bp.post("/runs/<run_id>/resume-from-stage")
@check_auth
@admin_required
def resume_from_stage(run_id: str):
    def operation():
        body = _payload()
        return _extension("research_training_runs").resume_from_stage(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            stage_id=body.get("stage_id"),
            expected_revision=body.get("expected_revision"),
        )

    return _invoke(operation)


@research_training_bp.post("/runs/<run_id>/clone")
@check_auth
@admin_required
def clone_run(run_id: str):
    def operation():
        body = _payload()
        key = str(request.headers.get("Idempotency-Key") or body.get("idempotency_key") or "")
        return _extension("research_training_runs").clone(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            idempotency_key=key,
        )

    return _invoke(operation, created=True)


@research_training_bp.post("/runs/<run_id>/claim")
@check_auth
@admin_required
def claim_run_stage(run_id: str):
    def operation():
        body = _payload()
        report = _extension("research_training_worker_registry").revalidate(
            worker_id=body.get("worker_id"),
            expected_report_digest=body.get("worker_inventory_digest"),
        )
        return _extension("research_training_runs").claim_next(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            worker_id=report["worker_id"],
            worker_inventory_digest=report["report_digest"],
            lease_seconds=int(body.get("lease_seconds") or 300),
            expected_revision=body.get("expected_revision"),
        )

    return _invoke(operation)


@research_training_bp.post("/runs/<run_id>/dispatch")
@check_auth
@admin_required
def dispatch_run_stage(run_id: str):
    def operation():
        body = _payload()
        if set(body) & {"tenant_id", "project_id", "worker_id"}:
            raise PermissionError("research_dispatch_authority_override_forbidden")
        return _extension("research_training_dispatch").prepare(
            tenant_id=_principal().tenant_id,
            project_id=_project_id(),
            run_id=run_id,
            **body,
        )

    return _invoke(operation, created=True)


@research_training_bp.post("/runs/<run_id>/worker-transition")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_transition(run_id: str):
    def operation():
        body = _payload()
        if set(body) & {"tenant_id", "run_id", "worker_id"}:
            raise PermissionError("research_worker_authority_override_forbidden")
        if body.get("target") != "failed":
            raise PermissionError("research_success_requires_result_ingress")
        assignment = _worker_assignment(body.pop("assignment_id", None))
        if assignment["run_id"] != run_id:
            raise PermissionError("research_worker_assignment_binding_invalid")
        tenant_id = assignment["run_spec"]["tenant_id"]
        worker_id = _authenticated_worker_id()
        run = _extension("research_training_runs").get(
            tenant_id=tenant_id,
            run_id=run_id,
        )
        stage = dict(run["stages"]).get(str(body.get("stage_id") or "")) or {}
        if stage.get("worker_id") != worker_id:
            raise PermissionError("research_worker_assignment_binding_invalid")
        return _extension("research_training_runs").transition(
            **body,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    return _invoke(operation)


@research_training_bp.post("/runs/<run_id>/heartbeat")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_heartbeat(run_id: str):
    def operation():
        body = _payload()
        assignment = _worker_assignment(body.pop("assignment_id", None))
        if assignment["run_id"] != run_id:
            raise PermissionError("research_worker_assignment_binding_invalid")
        tenant_id = assignment["run_spec"]["tenant_id"]
        worker_id = _authenticated_worker_id()
        run = _extension("research_training_runs").get(
            tenant_id=tenant_id,
            run_id=run_id,
        )
        stage = dict(run["stages"]).get(str(body.get("stage_id") or "")) or {}
        if stage.get("worker_id") != worker_id:
            raise PermissionError("research_worker_assignment_binding_invalid")
        return _extension("research_training_runs").heartbeat(
            **body,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    return _invoke(operation)


@research_training_bp.post("/runs/<run_id>/preempt")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_preempt(run_id: str):
    def operation():
        body = _payload()
        if set(body) != {"assignment_id", "result_ref"}:
            raise ValueError("research_preemption_payload_invalid")
        assignment = _worker_assignment(body["assignment_id"])
        return _extension("research_training_preemption_ingress").accept(
            tenant_id=assignment["run_spec"]["tenant_id"],
            project_id=assignment["dataset_manifest"]["project_id"],
            worker_id=_authenticated_worker_id(),
            assignment_id=str(body["assignment_id"]),
            result_ref=str(body["result_ref"]),
        )

    return _invoke(operation)


@research_training_bp.post("/results/ingress")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def worker_result_ingress():
    def operation():
        body = _payload()
        assignment = _worker_assignment(body.get("assignment_id"))
        return _extension("research_training_completion").complete(
            tenant_id=assignment["run_spec"]["tenant_id"],
            assignment_id=str(body.get("assignment_id") or ""),
            worker_id=_authenticated_worker_id(),
            result_ref=str(body.get("result_ref") or ""),
            project_id=assignment["dataset_manifest"]["project_id"],
            retention_class=str(body.get("retention_class") or "checkpoint"),
        )

    return _invoke(operation)


@research_training_bp.post("/evaluations")
@check_auth
@admin_required
def evaluate():
    return _invoke(lambda: _extension("research_training_evaluation").compare(**_payload()))


@research_training_bp.post("/quality-decision")
@check_auth
@admin_required
def quality_decision():
    return _invoke(lambda: _extension("research_training_quality_gate").decide(**_payload()))


@research_training_bp.post("/metrics")
@check_registered_worker_auth(scope=_WORKER_SCOPE)
def ingest_metric():
    def operation():
        body = _payload()
        assignment = _extension("research_training_assignments").resolve_attempt(
            worker_id=_authenticated_worker_id(),
            run_id=body.get("run_id"),
            stage_id=body.get("stage_id"),
            attempt_id=body.get("attempt_id"),
        )["assignment"]
        tenant_id = assignment["run_spec"]["tenant_id"]
        if body.get("tenant_id") != tenant_id:
            raise PermissionError("research_metric_tenant_binding_invalid")
        return _extension("research_training_telemetry").ingest(body)

    return _invoke(operation)


@research_training_bp.get("/runs/<run_id>/metrics")
@check_auth
@admin_required
def run_metrics(run_id: str):
    return _invoke(
        lambda: _extension("research_training_telemetry").list_run(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            limit=int(request.args.get("limit") or 500),
        )
    )


@research_training_bp.get("/runs/<run_id>/lineage")
@check_auth
@admin_required
def run_lineage(run_id: str):
    return _invoke(
        lambda: _extension("research_training_lineage").list_run(
            tenant_id=_principal().tenant_id,
            run_id=run_id,
            limit=int(request.args.get("limit") or 100),
        )
    )


@research_training_bp.post("/quota/reservations")
@check_auth
@admin_required
def reserve_quota():
    def operation():
        body = _payload()
        return _extension("research_training_quota").reserve(
            tenant_id=_principal().tenant_id,
            reservation_id=body.get("reservation_id"),
            expected_bytes=body.get("expected_bytes"),
            lease_seconds=body.get("lease_seconds"),
        )

    return _invoke(operation, created=True)


@research_training_bp.post("/retention/collect")
@check_auth
@admin_required
def collect_retention():
    def operation():
        body = _payload()
        return _extension("research_training_retention").collect(
            tenant_id=_principal().tenant_id,
            referenced_digests=body.get("referenced_digests") or [],
            limit=int(body.get("limit") or 100),
        )

    return _invoke(operation)


@research_training_bp.post("/release-decision")
@check_auth
@admin_required
def release_decision():
    return _invoke(lambda: _extension("research_training_release_gate").decide(**_payload()))


@research_training_bp.post("/promotion-decision")
@check_auth
@admin_required
def promotion_decision():
    def operation():
        body = _payload()
        if set(body) & {"tenant_id", "project_id"}:
            raise PermissionError("research_promotion_authority_override_forbidden")
        return _extension("research_training_promotion").decide(
            tenant_id=_principal().tenant_id,
            project_id=_project_id(),
            **body,
        )

    return _invoke(operation)


__all__ = ["research_training_bp"]
