"""Internal authenticated callbacks for the isolated speech-training worker."""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

from flask import Blueprint, current_app, jsonify, request

from agent.services.speech_adaptation_job_service import (
    SpeechAdaptationDecisionConflict,
)
from agent.services.speech_adaptation_production_composition import (
    HubSpeechAdaptationWorkerControl,
    SpeechAdaptationProductionConfigurationError,
)

speech_adaptation_control_bp = Blueprint(
    "speech_adaptation_control",
    __name__,
    url_prefix="/internal/v1/speech-adaptation-control",
)


@speech_adaptation_control_bp.before_request
def _authenticate():
    control = _control()
    value = str(request.headers.get("Authorization") or "")
    token = value[7:] if value.startswith("Bearer ") else ""
    if not control.authenticate(token):
        return _error("speech_training_callback_unauthorized", 401)
    return None


@speech_adaptation_control_bp.post("/authority")
def authority():
    if request.content_length is not None and request.content_length > 64 * 1024:
        return _error("speech_authority_request_too_large", 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("speech_authority_json_invalid", 400)
    active, reason = _control().authorize(payload)
    return jsonify({"active": active, "reason_code": reason}), 200


@speech_adaptation_control_bp.post("/artifacts")
def artifacts():
    metadata = _artifact_metadata()
    declared = metadata.get("size_bytes")
    if type(declared) is not int or declared < 1 or request.content_length != declared:
        return _error("speech_artifact_content_length_mismatch", 422)
    row = _control().publish_artifact(metadata, request.stream)
    return (
        jsonify(
            {
                "artifact_id": row.id,
                "artifact_ref": row.artifact_ref,
                "sha256": row.sha256,
                "size_bytes": row.size_bytes,
            }
        ),
        201,
    )


@speech_adaptation_control_bp.errorhandler(SpeechAdaptationProductionConfigurationError)
def configuration_error(exc: SpeechAdaptationProductionConfigurationError):
    status = 503 if exc.reason_code == "speech_training_control_unavailable" else 409
    return _error(exc.reason_code, status)


@speech_adaptation_control_bp.errorhandler(SpeechAdaptationDecisionConflict)
def decision_conflict(exc: SpeechAdaptationDecisionConflict):
    return _error(str(exc), 409)


def _control() -> HubSpeechAdaptationWorkerControl:
    value = current_app.extensions.get("speech_adaptation_worker_control")
    if not isinstance(value, HubSpeechAdaptationWorkerControl):
        raise SpeechAdaptationProductionConfigurationError("speech_training_control_unavailable")
    return value


def _artifact_metadata() -> Mapping[str, Any]:
    encoded = str(request.headers.get("X-Ananta-Artifact-Metadata") or "")
    if not encoded or len(encoded) > 8192:
        raise SpeechAdaptationProductionConfigurationError("speech_artifact_metadata_invalid")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise SpeechAdaptationProductionConfigurationError("speech_artifact_metadata_invalid") from exc
    if not isinstance(value, dict):
        raise SpeechAdaptationProductionConfigurationError("speech_artifact_metadata_invalid")
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON is forbidden")


def _error(reason_code: str, status_code: int):
    return (
        jsonify(
            {
                "error": {
                    "code": reason_code,
                    "message": "speech training callback was rejected",
                    "retryable": status_code >= 500,
                }
            }
        ),
        status_code,
    )


__all__ = ["speech_adaptation_control_bp"]
