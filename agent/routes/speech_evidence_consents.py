"""Authenticated Hub API for granular purpose-bound speech consent."""

from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, g, jsonify, request

from agent.auth import check_user_auth
from agent.services.speech_evidence_consent_service import (
    get_speech_evidence_consent_service,
)
from agent.services.speech_evidence_revocation_service import (
    SpeechEvidenceRevocationError,
    get_speech_evidence_revocation_service,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechEvidenceConsent,
    SpeechEvidenceGovernanceError,
)

speech_evidence_consents_bp = Blueprint("speech_evidence_consents", __name__)
_MAX_REQUEST_BYTES = 64 * 1024


@speech_evidence_consents_bp.post("/v1/voice/speech-evidence-consents")
@check_user_auth
def grant_speech_evidence_consent():
    try:
        _idempotency_key()
        consent = get_speech_evidence_consent_service().grant(
            _principal(),
            _json_body(),
        )
        return _response(consent, status_code=201)
    except SpeechEvidenceGovernanceError as exc:
        return _error(exc)


@speech_evidence_consents_bp.get(
    "/v1/voice/speech-evidence-consents/<consent_id>"
)
@check_user_auth
def get_speech_evidence_consent(consent_id: str):
    try:
        consent = get_speech_evidence_consent_service().get(
            _principal(),
            _identifier(consent_id, "speech_consent_id_invalid"),
        )
        return _response(consent)
    except SpeechEvidenceGovernanceError as exc:
        return _error(exc)


@speech_evidence_consents_bp.post(
    "/v1/voice/speech-evidence-consents/<consent_id>/<action>"
)
@check_user_auth
def mutate_speech_evidence_consent(consent_id: str, action: str):
    try:
        if action not in {"reduce", "renew", "revoke"}:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_action_not_found",
                "speech consent action was not found",
                status_code=404,
            )
        _idempotency_key()
        expected_version = _if_match()
        body = _json_body()
        service = get_speech_evidence_consent_service()
        principal = _principal()
        consent_id = _identifier(consent_id, "speech_consent_id_invalid")
        if action in {"reduce", "renew"}:
            if set(body) != {"consent"} or not isinstance(body["consent"], Mapping):
                raise SpeechEvidenceGovernanceError(
                    "speech_consent_mutation_shape_invalid",
                    "mutation body must contain exactly one consent object",
                )
            if str(body["consent"].get("consent_id") or "") != consent_id:
                raise SpeechEvidenceGovernanceError(
                    "speech_consent_id_mismatch",
                    "path and payload consent IDs differ",
                    status_code=409,
                )
            callback = service.reduce if action == "reduce" else service.renew
            result = callback(
                principal,
                body["consent"],
                expected_version=expected_version,
            )
        else:
            if set(body) - {"contributor_id"}:
                raise SpeechEvidenceGovernanceError(
                    "speech_consent_mutation_shape_invalid",
                    "revoke accepts only contributor_id",
                )
            contributor = body.get("contributor_id")
            if contributor is not None:
                contributor = _identifier(
                    contributor,
                    "speech_consent_contributor_invalid",
                )
            result = service.revoke(
                principal,
                consent_id,
                expected_version=expected_version,
                contributor_id=contributor,
            )
            cascade = get_speech_evidence_revocation_service().revoke_consent(
                principal,
                consent_id,
                expected_consent_version=result.consent_version,
                contributor_id=contributor,
            )
            return _response(result, revocation=cascade.public_dict())
        return _response(result)
    except (SpeechEvidenceGovernanceError, SpeechEvidenceRevocationError) as exc:
        return _error(exc)


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(
        identity.get("tenant_id") or identity.get("tenant") or subject
    ).strip()
    if not subject or not tenant_id:
        raise SpeechEvidenceGovernanceError(
            "speech_consent_unauthenticated",
            "authenticated tenant and subject are required",
            status_code=401,
        )
    return VoicePrincipal(tenant_id, subject)


def _json_body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise SpeechEvidenceGovernanceError(
            "speech_consent_request_too_large",
            "speech consent request exceeds its byte limit",
            status_code=413,
        )
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SpeechEvidenceGovernanceError(
            "speech_consent_json_invalid",
            "JSON object body is required",
            status_code=400,
        )
    return dict(value)


def _idempotency_key() -> str:
    key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not 8 <= len(key) <= 256 or any(character.isspace() for character in key):
        raise SpeechEvidenceGovernanceError(
            "speech_consent_idempotency_key_invalid",
            "a bounded Idempotency-Key is required",
            status_code=400,
        )
    return key


def _if_match() -> int:
    value = str(request.headers.get("If-Match") or "").removeprefix("W/").strip().strip('"')
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise SpeechEvidenceGovernanceError(
            "speech_consent_precondition_required",
            "If-Match must contain the expected consent version",
            status_code=428,
        ) from exc
    if isinstance(version, bool) or not 1 <= version <= 2_147_483_647:
        raise SpeechEvidenceGovernanceError(
            "speech_consent_precondition_invalid",
            "If-Match consent version is invalid",
            status_code=400,
        )
    return version


def _identifier(value: object, reason_code: str) -> str:
    rendered = str(value or "").strip()
    if not 1 <= len(rendered) <= 160 or any(
        not (character.isalnum() or character in "._:@-")
        for character in rendered
    ):
        raise SpeechEvidenceGovernanceError(
            reason_code,
            "identifier is invalid",
            status_code=400,
        )
    return rendered


def _response(
    consent: SpeechEvidenceConsent,
    *,
    status_code: int = 200,
    revocation: Mapping[str, object] | None = None,
):
    data: dict[str, object] = {
        "consent": consent.to_dict(),
        "consent_digest": consent.consent_digest,
        "scope_digest": consent.scope_digest,
    }
    if revocation is not None:
        data["revocation"] = dict(revocation)
    return jsonify(
        {
            "ok": True,
            "data": data,
        }
    ), status_code


def _error(exc: SpeechEvidenceGovernanceError | SpeechEvidenceRevocationError):
    return jsonify(
        {
            "ok": False,
            "error": {
                "code": exc.reason_code,
                "retriable": exc.status_code >= 500,
            },
        }
    ), exc.status_code


__all__ = ["speech_evidence_consents_bp"]
