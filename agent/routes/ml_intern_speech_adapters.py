"""Additive admin API for pair-scoped speech adapter lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.ml_intern_speech_adapter_registry import (
    MlInternSpeechAdapterRegistry,
    SpeechAdapterRegistryError,
)
from agent.services.ml_intern_speech_eval_service import MlInternSpeechEvalService
from agent.services.speech_adaptation_job_service import (
    SpeechAdaptationAdmissionError,
    SpeechAdaptationJobService,
    SpeechPrincipal,
)
from ananta_contracts.speech_adaptation_evaluation import SpeechEvaluationError

ml_intern_speech_adapters_bp = Blueprint(
    "ml_intern_speech_adapters",
    __name__,
    url_prefix="/api/ml-intern-speech-adapters",
)


@ml_intern_speech_adapters_bp.post("/training-jobs")
@check_auth
@admin_required
def admit_training_job():
    tenant, subject = _principal()
    service = _job_service()
    decision = service.admit(
        SpeechPrincipal(tenant, subject),
        _json_body(),
        idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
    )
    return api_response(data=_job_read_model(decision), code=202 if decision.status == "queued" else 200)


@ml_intern_speech_adapters_bp.get("/training-jobs/<job_id>")
@check_auth
@admin_required
def get_training_job(job_id: str):
    tenant, subject = _principal()
    return api_response(data=_job_read_model(_job_service().get(SpeechPrincipal(tenant, subject), job_id)))


@ml_intern_speech_adapters_bp.get("/training-jobs/<job_id>/evaluation")
@check_auth
@admin_required
def get_training_evaluation(job_id: str):
    tenant, subject = _principal()
    report = _job_service().evaluation_report(
        SpeechPrincipal(tenant, subject),
        job_id,
    )
    return api_response(data={"report": dict(report)})


@ml_intern_speech_adapters_bp.post("/training-jobs/<job_id>/cancel")
@check_auth
@admin_required
def cancel_training_job(job_id: str):
    tenant, subject = _principal()
    payload = _json_body()
    if set(payload) != {"confirmed", "reason_code"} or payload.get("confirmed") is not True:
        raise SpeechAdaptationAdmissionError(
            "speech_cancel_confirmation_required",
            "explicit speech adaptation cancellation is required",
        )
    decision = _job_service().cancel(
        SpeechPrincipal(tenant, subject),
        job_id,
        reason_code=str(payload.get("reason_code") or ""),
    )
    return api_response(data=_job_read_model(decision))


@ml_intern_speech_adapters_bp.get("")
@check_auth
def list_adapters():
    tenant, subject = _principal()
    pair_id, direction = _pair_scope()
    records = _registry().list_for_pair(
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=pair_id,
        direction=direction,
    )
    return api_response(data={"items": [item.public_dict() for item in records], "count": len(records)})


@ml_intern_speech_adapters_bp.get("/<adapter_id>")
@check_auth
def get_adapter(adapter_id: str):
    tenant, subject = _principal()
    pair_id, direction = _pair_scope()
    record = _registry().get_for_pair(
        adapter_id,
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=pair_id,
        direction=direction,
    )
    return api_response(data=record.public_dict())


@ml_intern_speech_adapters_bp.post("/register-evaluated")
@check_auth
@admin_required
def register_evaluated():
    tenant, subject = _principal()
    payload = _json_body()
    expected = {
        "adapter_id",
        "version",
        "pair_id",
        "direction",
        "speaker_digest",
        "scope_digest",
        "base_model_id",
        "base_model_digest",
        "backend",
        "backend_digest",
        "dataset_digest",
        "split_digest",
        "consent_digest",
        "consent_expires_at_ms",
        "artifact_ref",
        "artifact_sha256",
        "artifact_size_bytes",
        "expires_at_ms",
        "evaluation_report",
    }
    if set(payload) != expected:
        raise SpeechAdapterRegistryError(
            "speech_adapter_register_shape_invalid",
            "speech adapter registration has unknown or missing fields",
        )
    report = payload.get("evaluation_report")
    if not isinstance(report, Mapping):
        raise SpeechAdapterRegistryError("speech_evaluation_report_invalid", "evaluation report is required")
    bindings = {
        "dataset_digest": str(payload["dataset_digest"]),
        "split_digest": str(payload["split_digest"]),
        "model_digest": str(payload["base_model_digest"]),
        "config_digest": str(report.get("bindings", {}).get("config_digest", "")),
        "scope_digest": str(payload["scope_digest"]),
        "consent_digest": str(payload["consent_digest"]),
    }
    decision = MlInternSpeechEvalService().decide(report, expected_bindings=bindings)
    admission = current_app.extensions.get("speech_adapter_registration_admission")
    verifier = getattr(admission, "verify_registration", None)
    if not callable(verifier):
        raise SpeechAdapterRegistryError(
            "speech_adapter_registration_admission_unavailable",
            "Hub training artifact admission is not configured",
            status_code=503,
        )
    admitted, reason = verifier(
        SpeechPrincipal(tenant, subject),
        payload,
        evaluation_report_digest=decision.report_digest,
    )
    if not admitted:
        raise SpeechAdapterRegistryError(
            str(reason or "speech_adapter_training_binding_mismatch"),
            "adapter is not bound to a completed Hub training result",
            status_code=409,
        )
    record = _registry().register_evaluated(
        adapter_id=str(payload["adapter_id"]),
        version=str(payload["version"]),
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=str(payload["pair_id"]),
        direction=str(payload["direction"]),
        speaker_digest=str(payload["speaker_digest"]),
        scope_digest=str(payload["scope_digest"]),
        base_model_id=str(payload["base_model_id"]),
        base_model_digest=str(payload["base_model_digest"]),
        backend=str(payload["backend"]),
        backend_digest=str(payload["backend_digest"]),
        dataset_digest=str(payload["dataset_digest"]),
        split_digest=str(payload["split_digest"]),
        evaluation=decision,
        consent_digest=str(payload["consent_digest"]),
        consent_expires_at_ms=int(payload["consent_expires_at_ms"]),
        artifact_ref=str(payload["artifact_ref"]),
        artifact_sha256=str(payload["artifact_sha256"]),
        artifact_size_bytes=int(payload["artifact_size_bytes"]),
        expires_at_ms=int(payload["expires_at_ms"]),
    )
    return api_response(data=record.public_dict(), code=201)


@ml_intern_speech_adapters_bp.post("/<adapter_id>/approve")
@check_auth
@admin_required
def approve(adapter_id: str):
    tenant, subject = _principal()
    payload = _json_body()
    _closed_action(
        payload,
        {"pair_id", "direction", "expected_version", "confirmed", "reason_code", "consent_digest"},
    )
    record = _registry().approve(
        adapter_id,
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=str(payload["pair_id"]),
        direction=str(payload["direction"]),
        expected_version=int(payload["expected_version"]),
        authorized_confirmation=payload["confirmed"] is True,
        approved_by=subject,
        reason_code=str(payload["reason_code"]),
        current_consent_digest=str(payload["consent_digest"]),
    )
    return api_response(data=record.public_dict())


@ml_intern_speech_adapters_bp.post("/<adapter_id>/<action>")
@check_auth
@admin_required
def transition(adapter_id: str, action: str):
    if action not in {"revoke", "deprecate", "expire"}:
        raise SpeechAdapterRegistryError(
            "speech_adapter_action_invalid",
            "speech adapter action is invalid",
            status_code=404,
        )
    tenant, subject = _principal()
    payload = _json_body()
    _closed_action(payload, {"pair_id", "direction", "expected_version", "confirmed", "reason_code"})
    if payload["confirmed"] is not True:
        raise SpeechAdapterRegistryError("speech_adapter_confirmation_required", "explicit confirmation is required")
    record = _registry().change_status(
        adapter_id,
        target={"revoke": "revoked", "deprecate": "deprecated", "expire": "expired"}[action],
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=str(payload["pair_id"]),
        direction=str(payload["direction"]),
        expected_version=int(payload["expected_version"]),
        actor=subject,
        reason_code=str(payload["reason_code"]),
    )
    return api_response(data=record.public_dict())


@ml_intern_speech_adapters_bp.post("/<adapter_id>/rollback")
@check_auth
@admin_required
def rollback(adapter_id: str):
    tenant, subject = _principal()
    payload = _json_body()
    _closed_action(
        payload,
        {
            "pair_id",
            "direction",
            "expected_version",
            "target_adapter_id",
            "target_expected_version",
            "confirmed",
            "reason_code",
        },
    )
    if payload["confirmed"] is not True:
        raise SpeechAdapterRegistryError("speech_adapter_confirmation_required", "explicit confirmation is required")
    record = _registry().rollback(
        from_adapter_id=adapter_id,
        to_adapter_id=str(payload["target_adapter_id"]),
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=str(payload["pair_id"]),
        direction=str(payload["direction"]),
        from_expected_version=int(payload["expected_version"]),
        to_expected_version=int(payload["target_expected_version"]),
        actor=subject,
        reason_code=str(payload["reason_code"]),
    )
    return api_response(data=record.public_dict())


@ml_intern_speech_adapters_bp.post("/<adapter_id>/export")
@check_auth
@admin_required
def export(adapter_id: str):
    tenant, subject = _principal()
    payload = _json_body()
    _closed_action(
        payload,
        {
            "pair_id",
            "direction",
            "expected_version",
            "confirmed",
            "export_consent_digest",
            "export_consent_epoch",
            "destination_ref",
        },
    )
    if payload["confirmed"] is not True:
        raise SpeechAdapterRegistryError("speech_adapter_export_confirmation_required", "explicit export is required")
    export_port = current_app.extensions.get("speech_adapter_export_port")
    if export_port is None:
        raise SpeechAdapterRegistryError(
            "speech_adapter_export_unavailable",
            "encrypted speech adapter export is not configured",
            status_code=503,
        )
    receipt = _registry().export_encrypted(
        adapter_id,
        tenant_id=tenant,
        owner_subject=subject,
        pair_id=str(payload["pair_id"]),
        direction=str(payload["direction"]),
        expected_version=int(payload["expected_version"]),
        export_consent_digest=str(payload["export_consent_digest"]),
        export_consent_epoch=payload["export_consent_epoch"],
        destination_ref=str(payload["destination_ref"]),
        export_port=export_port,
    )
    # Receipt intentionally contains no key, server path or source artifact.
    return api_response(
        data={
            "export_id": receipt.export_id,
            "encrypted_artifact_ref": receipt.encrypted_artifact_ref,
            "ciphertext_sha256": receipt.ciphertext_sha256,
            "size_bytes": receipt.size_bytes,
            "encryption_scheme": receipt.encryption_scheme,
        }
    )


@ml_intern_speech_adapters_bp.errorhandler(SpeechAdapterRegistryError)
def registry_error(exc: SpeechAdapterRegistryError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.reason_code, "message": str(exc), "retryable": exc.status_code >= 500}},
    )


@ml_intern_speech_adapters_bp.errorhandler(SpeechAdaptationAdmissionError)
def admission_error(exc: SpeechAdaptationAdmissionError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={"error": {"code": exc.reason_code, "message": str(exc), "retryable": exc.status_code >= 500}},
    )


@ml_intern_speech_adapters_bp.errorhandler(SpeechEvaluationError)
def evaluation_error(exc: SpeechEvaluationError):
    return api_response(
        status="error",
        code=422,
        data={"error": {"code": exc.reason_code, "message": str(exc), "retryable": False}},
    )


def _registry() -> MlInternSpeechAdapterRegistry:
    cached = current_app.extensions.get("ml_intern_speech_adapter_registry")
    if isinstance(cached, MlInternSpeechAdapterRegistry):
        return cached
    agent_config = dict(current_app.config.get("AGENT_CONFIG", {}) or {})
    speech_config = dict(agent_config.get("speech_adaptation") or {})
    path = Path(
        os.getenv("ANANTA_SPEECH_ADAPTER_REGISTRY_PATH")
        or speech_config.get("adapter_registry_path")
        or "artifacts/speech-adapters/registry.json"
    )
    registry = MlInternSpeechAdapterRegistry(
        path,
        audit_sink=log_audit,
        authority_audit=current_app.extensions.get("semantic_media_audit_recorder"),
    )
    current_app.extensions["ml_intern_speech_adapter_registry"] = registry
    return registry


def _job_service() -> SpeechAdaptationJobService:
    service = current_app.extensions.get("speech_adaptation_job_service")
    if not isinstance(service, SpeechAdaptationJobService):
        raise SpeechAdaptationAdmissionError(
            "speech_training_admission_unavailable",
            "speech training admission ports are not configured",
            status_code=503,
        )
    return service


def _job_read_model(decision) -> dict[str, Any]:
    result = decision.result
    return {
        "job_id": decision.job_id,
        "task_id": decision.task_id,
        "status": decision.status,
        "reason_code": decision.reason_code,
        "binding_digest": decision.job.binding_digest if decision.job is not None else None,
        "attempt_id": decision.job.attempt.attempt_id if decision.job is not None else None,
        "deadline_at_ms": decision.job.deadline_at_ms if decision.job is not None else None,
        "result": (
            {
                "status": result.status,
                "reason_code": result.reason_code,
                "events_digest": result.events_digest,
                "evaluation_report_digest": result.evaluation_report_digest,
                "checkpoint_digest": result.checkpoint_digest,
                "artifact": (
                    {
                        "artifact_id": result.artifact.artifact_id,
                        "artifact_ref": result.artifact.artifact_ref,
                        "sha256": result.artifact.sha256,
                        "size_bytes": result.artifact.size_bytes,
                        "media_type": result.artifact.media_type,
                    }
                    if result.artifact is not None
                    else None
                ),
            }
            if result is not None
            else None
        ),
    }


def _principal() -> tuple[str, str]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "hub-admin").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return tenant, subject


def _pair_scope() -> tuple[str, str]:
    if set(request.args) != {"pair_id", "direction"}:
        raise SpeechAdapterRegistryError(
            "speech_adapter_pair_scope_shape_invalid",
            "only pair_id and direction query fields are accepted",
        )
    pair_id = str(request.args.get("pair_id") or "").strip()
    direction = str(request.args.get("direction") or "").strip()
    if not pair_id or len(pair_id) > 192 or direction not in {"sender_to_receiver", "receiver_to_sender"}:
        raise SpeechAdapterRegistryError("speech_adapter_pair_scope_required", "pair_id and direction are required")
    return pair_id, direction


def _json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise SpeechAdapterRegistryError("speech_adapter_json_invalid", "JSON object body is required", status_code=400)
    return payload


def _closed_action(payload: Mapping[str, Any], fields: set[str]) -> None:
    if set(payload) != fields:
        raise SpeechAdapterRegistryError(
            "speech_adapter_action_shape_invalid",
            "speech adapter action has unknown or missing fields",
        )
