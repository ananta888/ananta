"""Authenticated Hub boundary for experimental HRM capabilities and preflight."""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_service_auth, check_strict_auth
from agent.repositories.hrm_experiments import HrmRepositoryConflict
from agent.services.hrm_experiments import (
    HrmContractValidationError,
    HrmExperimentControlPlaneService,
    default_hrm_experiment_control_plane_service,
)
from agent.services.hrm_experiments.admission import HrmAdmissionError
from agent.services.hrm_experiments.application import (
    HrmApplicationError,
    HrmExperimentApplicationService,
    HrmPrincipal,
    default_hrm_experiment_application_service,
)
from agent.services.hrm_experiments.artifact_store import HrmArtifactStoreError
from agent.services.workflow_worker_service_auth import HRM_EXPERIMENT_WORKER_SCOPE

hrm_experiments_bp = Blueprint(
    "hrm_experiments",
    __name__,
    url_prefix="/api/hrm-experiments",
)

HRM_CONTROL_PLANE_EXTENSION = "hrm_experiment_control_plane_service"
HRM_APPLICATION_EXTENSION = "hrm_experiment_application_service"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PREFLIGHT_FIELDS = frozenset({"project_id", "profile_id"})
_PAGE_QUERY_FIELDS = frozenset({"project_id", "cursor", "limit"})
_PROJECT_QUERY_FIELDS = frozenset({"project_id"})
_MAX_BODY_BYTES = 2 * 1024 * 1024


def _problem(reason_code: str, status_code: int):
    return jsonify({"status": "error", "reason_code": reason_code}), status_code


def _control_plane() -> HrmExperimentControlPlaneService:
    service = current_app.extensions.get(HRM_CONTROL_PLANE_EXTENSION)
    if service is None:
        service = default_hrm_experiment_control_plane_service()
    return service


def _application() -> HrmExperimentApplicationService:
    service = current_app.extensions.get(HRM_APPLICATION_EXTENSION)
    if service is None:
        service = default_hrm_experiment_application_service()
    return service


def _principal() -> HrmPrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject or not tenant_id:
        raise HrmApplicationError("hrm.not_authenticated", status_code=401)
    return HrmPrincipal(tenant_id=tenant_id, subject=subject)


def _worker_identity() -> tuple[str, str]:
    identity = dict(getattr(g, "service_identity", {}) or {})
    worker_id = str(identity.get("worker_id") or request.headers.get("X-Ananta-Worker-ID") or "").strip()
    worker_url = str(identity.get("worker_url") or request.headers.get("X-Ananta-Worker-URL") or "").strip().rstrip("/")
    if not _valid_identifier(worker_id) or not worker_url:
        raise HrmApplicationError("hrm.worker_identity_required", status_code=403)
    return worker_id, worker_url


def _json_object(*, fields: frozenset[str] | None = None) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_BODY_BYTES:
        raise HrmApplicationError("hrm.request_too_large", status_code=413)
    if not request.is_json:
        raise HrmApplicationError("hrm.json_required", status_code=415)
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or (fields is not None and set(value) != fields):
        raise HrmApplicationError("hrm.request_invalid", status_code=400)
    return value


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(value) <= 191 or any(character.isspace() for character in value):
        raise HrmApplicationError("hrm.idempotency_key_invalid", status_code=400)
    return value


def _project_id(*, page: bool = False) -> str:
    allowed = _PAGE_QUERY_FIELDS if page else _PROJECT_QUERY_FIELDS
    if set(request.args).difference(allowed):
        raise HrmApplicationError("hrm.query_forbidden", status_code=400)
    value = str(request.args.get("project_id") or "")
    if not _valid_identifier(value):
        raise HrmApplicationError("hrm.project_id_invalid", status_code=400)
    return value


def _page_values() -> tuple[str | None, int]:
    cursor = request.args.get("cursor")
    if cursor is not None and len(cursor) > 2048:
        raise HrmApplicationError("hrm.cursor_invalid", status_code=400)
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError as exc:
        raise HrmApplicationError("hrm.limit_invalid", status_code=400) from exc
    if not 1 <= limit <= 200:
        raise HrmApplicationError("hrm.limit_invalid", status_code=400)
    return cursor, limit


def _route_error(exc: Exception):
    if isinstance(exc, HrmApplicationError):
        return _problem(exc.reason_code, exc.status_code)
    reason_code = str(getattr(exc, "reason_code", "") or "hrm.operation_failed")
    status_code = 409
    if isinstance(exc, HrmContractValidationError):
        status_code = 400
    if isinstance(exc, (OSError, HrmArtifactStoreError)):
        status_code = 503
    return _problem(reason_code, status_code)


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


@hrm_experiments_bp.get("/capabilities")
@check_strict_auth
def list_hrm_experiment_capabilities():
    if request.args:
        return _problem("hrm.capability_query_forbidden", 400)
    try:
        return jsonify(_control_plane().capability()), 200
    except (HrmContractValidationError, OSError, ValueError):
        return _problem("hrm.capability_unavailable", 503)


@hrm_experiments_bp.post("/preflight")
@check_strict_auth
def preflight_hrm_experiment():
    if not request.is_json:
        return _problem("hrm.preflight_json_required", 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _problem("hrm.preflight_request_invalid", 400)
    if set(payload) != _PREFLIGHT_FIELDS:
        return _problem("hrm.preflight_request_invalid", 400)
    project_id = payload.get("project_id")
    profile_id = payload.get("profile_id")
    if not _valid_identifier(project_id) or not _valid_identifier(profile_id):
        return _problem("hrm.preflight_request_invalid", 400)
    try:
        result = _control_plane().preflight(
            project_id=project_id,
            profile_id=profile_id,
        )
    except HrmContractValidationError:
        return _problem("hrm.preflight_unavailable", 503)
    except OSError:
        return _problem("hrm.preflight_unavailable", 503)
    except ValueError:
        return _problem("hrm.preflight_request_invalid", 400)
    return jsonify(result), 200


@hrm_experiments_bp.get("/datasets")
@check_strict_auth
def list_hrm_puzzle_datasets():
    try:
        project_id = _project_id(page=True)
        cursor, limit = _page_values()
        return jsonify(
            _application().list_datasets(
                _principal(), project_id=project_id, cursor=cursor, limit=limit
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/datasets")
@check_strict_auth
def register_hrm_puzzle_dataset():
    try:
        result = _application().register_dataset(
            _principal(), _json_object(), idempotency_key=_idempotency_key()
        )
        return jsonify(result), 201
    except (HrmApplicationError, HrmAdmissionError, HrmArtifactStoreError, HrmContractValidationError, HrmRepositoryConflict) as exc:
        return _route_error(exc)


@hrm_experiments_bp.get("/checkpoints")
@check_strict_auth
def list_hrm_checkpoints():
    try:
        project_id = _project_id(page=True)
        cursor, limit = _page_values()
        return jsonify(
            _application().list_checkpoints(
                _principal(), project_id=project_id, cursor=cursor, limit=limit
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/checkpoints")
@check_strict_auth
def admit_hrm_checkpoint():
    try:
        result = _application().admit_checkpoint(
            _principal(), _json_object(), idempotency_key=_idempotency_key()
        )
        return jsonify(result), 202
    except (HrmApplicationError, HrmAdmissionError, HrmArtifactStoreError, HrmContractValidationError, HrmRepositoryConflict) as exc:
        return _route_error(exc)


@hrm_experiments_bp.get("/runs")
@check_strict_auth
def list_hrm_experiment_runs():
    try:
        project_id = _project_id(page=True)
        cursor, limit = _page_values()
        return jsonify(
            _application().list_runs(
                _principal(), project_id=project_id, cursor=cursor, limit=limit
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/runs")
@check_strict_auth
def start_hrm_experiment_run():
    try:
        result, replayed = _application().start_run(
            _principal(), _json_object(), idempotency_key=_idempotency_key()
        )
        return jsonify({**result, "idempotent_replay": replayed}), 202
    except (HrmApplicationError, HrmContractValidationError, HrmRepositoryConflict) as exc:
        return _route_error(exc)


@hrm_experiments_bp.get("/runs/<run_id>")
@check_strict_auth
def get_hrm_experiment_run(run_id: str):
    try:
        if not _valid_identifier(run_id):
            raise HrmApplicationError("hrm.run_id_invalid", status_code=400)
        return jsonify(
            _application().get_run(
                _principal(), project_id=_project_id(), run_id=run_id
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.get("/runs/<run_id>/events")
@check_strict_auth
def list_hrm_experiment_run_events(run_id: str):
    try:
        if not _valid_identifier(run_id):
            raise HrmApplicationError("hrm.run_id_invalid", status_code=400)
        project_id = _project_id(page=True)
        cursor, limit = _page_values()
        return jsonify(
            _application().list_events(
                _principal(),
                project_id=project_id,
                run_id=run_id,
                cursor=cursor,
                limit=limit,
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/runs/<run_id>/cancel")
@check_strict_auth
def cancel_hrm_experiment_run(run_id: str):
    try:
        if not _valid_identifier(run_id):
            raise HrmApplicationError("hrm.run_id_invalid", status_code=400)
        return jsonify(
            _application().cancel_run(
                _principal(),
                project_id=_project_id(),
                run_id=run_id,
                request_payload=_json_object(),
                idempotency_key=_idempotency_key(),
            )
        ), 200
    except (HrmApplicationError, HrmContractValidationError) as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/evaluations")
@check_strict_auth
def create_hrm_evaluation():
    try:
        body = _json_object(fields=frozenset({"project_id", "run_id"}))
        if not _valid_identifier(body["project_id"]) or not _valid_identifier(body["run_id"]):
            raise HrmApplicationError("hrm.evaluation_request_invalid", status_code=400)
        return jsonify(
            _application().create_evaluation(
                _principal(),
                project_id=body["project_id"],
                run_id=body["run_id"],
                idempotency_key=_idempotency_key(),
            )
        ), 202
    except (HrmApplicationError, HrmContractValidationError, HrmRepositoryConflict) as exc:
        return _route_error(exc)


@hrm_experiments_bp.get("/reports/<report_id>")
@check_strict_auth
def get_hrm_evaluation_report(report_id: str):
    try:
        if not _valid_identifier(report_id):
            raise HrmApplicationError("hrm.report_id_invalid", status_code=400)
        return jsonify(
            _application().get_report(
                _principal(), project_id=_project_id(), report_id=report_id
            )
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/internal/capabilities")
@check_service_auth(scope=HRM_EXPERIMENT_WORKER_SCOPE)
def advertise_hrm_worker_capability():
    try:
        body = _json_object(fields=frozenset({"capability", "ttl_seconds"}))
        worker_id, worker_url = _worker_identity()
        return jsonify(
            {
                "data": _application().advertise_capability(
                    worker_id=worker_id,
                    worker_url=worker_url,
                    capability=body["capability"],
                    ttl_seconds=int(body["ttl_seconds"]),
                )
            }
        ), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/internal/authorize")
@check_service_auth(scope=HRM_EXPERIMENT_WORKER_SCOPE)
def authorize_hrm_worker_execution():
    try:
        body = _json_object(
            fields=frozenset({"run_id", "task_id", "worker_job_id"})
        )
        _worker_id, worker_url = _worker_identity()
        if any(not _valid_identifier(body[key]) for key in body):
            raise HrmApplicationError("hrm.authorization_request_invalid", status_code=400)
        execution = _application().authorize_execution(
            run_id=body["run_id"],
            task_id=body["task_id"],
            worker_job_id=body["worker_job_id"],
            worker_url=worker_url,
        )
        return jsonify({"data": {"authorized": True, "execution": execution}}), 200
    except Exception as exc:
        return _route_error(exc)


@hrm_experiments_bp.post("/internal/results")
@check_service_auth(scope=HRM_EXPERIMENT_WORKER_SCOPE)
def admit_hrm_worker_result():
    try:
        body = _json_object(fields=frozenset({"run_id", "result"}))
        _worker_id, worker_url = _worker_identity()
        if not _valid_identifier(body["run_id"]) or not isinstance(body["result"], dict):
            raise HrmApplicationError("hrm.result_request_invalid", status_code=400)
        result = _application().submit_result(
            run_id=body["run_id"], worker_url=worker_url, result=body["result"]
        )
        return jsonify({"data": result}), 200
    except Exception as exc:
        return _route_error(exc)


__all__ = [
    "HRM_APPLICATION_EXTENSION",
    "HRM_CONTROL_PLANE_EXTENSION",
    "hrm_experiments_bp",
]
