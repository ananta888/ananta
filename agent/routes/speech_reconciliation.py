"""Thin authenticated API for Hub-owned speech reconciliation."""

from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, current_app, g, jsonify, request

from agent.auth import check_user_auth
from agent.services.speech_reconciliation_job_service import (
    SpeechReconciliationJobService,
    SpeechReconciliationJobServiceError,
    build_speech_reconciliation_job_service,
)
from agent.services.speech_reconciliation_read_model_service import SpeechReconciliationReadModelService
from agent.services.voice_governance_domain import VoicePrincipal

speech_reconciliation_bp = Blueprint("speech_reconciliation", __name__)

_MAX_REQUEST_BYTES = 64 * 1024
_MUTATION_FIELDS = frozenset({"expected_version"})
_REDUCE_FIELDS = frozenset({"expected_version", "max_compute_factor"})


@speech_reconciliation_bp.post("/v1/voice/speech-reconciliation")
@check_user_auth
def create_speech_reconciliation():
    try:
        body = _json_body()
        admission = _service().create(
            _principal(),
            body,
            idempotency_key=_idempotency_key(),
        )
        result = _read_model().project(admission.job)
        result["budget_plan"] = {
            "compute_factor": admission.budget.compute_factor,
            "compute_equivalent_ms": admission.budget.compute_equivalent_ms,
            "allocated": admission.budget.total.to_dict(),
        }
        return jsonify({"ok": True, "data": {"job": result}}), 201 if admission.created else 200
    except SpeechReconciliationJobServiceError as exc:
        return _error(exc)


@speech_reconciliation_bp.get("/v1/voice/speech-reconciliation")
@check_user_auth
def list_speech_reconciliation():
    try:
        if set(request.args) - {"offset", "limit"}:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_query_invalid")
        offset = _query_int("offset", default=0, minimum=0, maximum=1_000_000)
        limit = _query_int("limit", default=50, minimum=1, maximum=100)
        rows = _service().list(_principal(), offset=offset, limit=limit)
        return jsonify(
            {
                "ok": True,
                "data": {
                    "jobs": [_read_model().project(row) for row in rows],
                    "next_offset": offset + len(rows) if len(rows) == limit else None,
                },
            }
        ), 200
    except SpeechReconciliationJobServiceError as exc:
        return _error(exc)


@speech_reconciliation_bp.get("/v1/voice/speech-reconciliation/<job_id>")
@check_user_auth
def get_speech_reconciliation(job_id: str):
    try:
        return jsonify({"ok": True, "data": {"job": _read_model().project(_service().get(_principal(), job_id))}}), 200
    except SpeechReconciliationJobServiceError as exc:
        return _error(exc)


@speech_reconciliation_bp.post("/v1/voice/speech-reconciliation/<job_id>/<action>")
@check_user_auth
def mutate_speech_reconciliation(job_id: str, action: str):
    try:
        if action not in {"pause", "resume", "cancel", "reduce"}:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_action_not_found", status_code=404)
        body = _json_body()
        expected_fields = _REDUCE_FIELDS if action == "reduce" else _MUTATION_FIELDS
        if set(body) != expected_fields:
            raise SpeechReconciliationJobServiceError("speech_reconciliation_mutation_shape_invalid")
        expected_version = _version_precondition(body.get("expected_version"))
        idempotency_key = _idempotency_key()
        service = _service()
        if action == "reduce":
            job = service.reduce(
                _principal(),
                job_id,
                expected_version=expected_version,
                max_compute_factor=_body_int(body.get("max_compute_factor"), 1, 100),
                idempotency_key=idempotency_key,
            )
        else:
            mutation = getattr(service, action)
            job = mutation(
                _principal(),
                job_id,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
            )
        return jsonify({"ok": True, "data": {"job": _read_model().project(job)}}), 200
    except SpeechReconciliationJobServiceError as exc:
        return _error(exc)


def _service() -> SpeechReconciliationJobService:
    configured = current_app.extensions.get("speech_reconciliation_job_service")
    if configured is not None:
        return configured
    service = build_speech_reconciliation_job_service(
        audit=current_app.extensions.get("semantic_media_audit_recorder"),
    )
    current_app.extensions["speech_reconciliation_job_service"] = service
    return service


def _read_model() -> SpeechReconciliationReadModelService:
    configured = current_app.extensions.get("speech_reconciliation_read_model")
    if configured is not None:
        return configured
    model = SpeechReconciliationReadModelService()
    current_app.extensions["speech_reconciliation_read_model"] = model
    return model


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject or not tenant_id:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_unauthenticated", status_code=401)
    return VoicePrincipal(tenant_id, subject)


def _json_body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_request_too_large", status_code=413)
    body = request.get_json(silent=True)
    if not isinstance(body, Mapping) or any(not isinstance(key, str) for key in body):
        raise SpeechReconciliationJobServiceError("speech_reconciliation_json_invalid", status_code=400)
    return dict(body)


def _idempotency_key() -> str:
    value = str(request.headers.get("Idempotency-Key") or "").strip()
    if not value:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_idempotency_key_required", status_code=400)
    return value


def _version_precondition(body_value: Any) -> int:
    expected = _body_int(body_value, 1, 2**31 - 1)
    raw_header = str(request.headers.get("If-Match") or "").strip()
    if not raw_header:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_precondition_required", status_code=428)
    header = raw_header.removeprefix("W/").strip().strip('"')
    try:
        parsed = int(header)
    except ValueError as exc:
        raise SpeechReconciliationJobServiceError(
            "speech_reconciliation_precondition_invalid", status_code=400
        ) from exc
    if parsed != expected:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_precondition_mismatch", status_code=412)
    return expected


def _body_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_integer_invalid")
    return value


def _query_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_pagination_invalid") from exc
    if not minimum <= value <= maximum:
        raise SpeechReconciliationJobServiceError("speech_reconciliation_pagination_invalid")
    return value


def _error(exc: SpeechReconciliationJobServiceError):
    return jsonify(
        {"ok": False, "error": {"code": exc.reason_code, "retriable": exc.status_code >= 500}}
    ), exc.status_code


__all__ = ["speech_reconciliation_bp"]
