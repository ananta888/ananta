"""Authenticated additive API for Hub-authorized peer speech-evidence sync."""

from __future__ import annotations

import re
from typing import Any, Mapping

from flask import Blueprint, current_app, g, jsonify, request
from sqlmodel import Session, select

from agent.auth import check_service_auth, check_user_auth
from agent.database import engine
from agent.db_models.speech_evidence import SpeechPeerEvidenceCurationDB
from agent.services.speech_evidence_peer_curation_composition import (
    SpeechPeerCurationError,
    SpeechPeerEvidenceCurationService,
)
from agent.services.speech_evidence_sync_composition import (
    HubSpeechEvidenceSyncError,
    HubSpeechEvidenceSyncService,
    offer_public_dict,
    speech_evidence_sync_error,
)
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.workflow_worker_service_auth import SPEECH_EVIDENCE_CURATION_WORKER_SCOPE

speech_evidence_sync_bp = Blueprint("speech_evidence_sync", __name__)

# A relayed chunk carries both the bounded signed inner message and an opaque,
# pair-encrypted outer envelope.  The combined request is still hard-capped.
_MAX_REQUEST_BYTES = 768 * 1024
_MAX_CURATION_REQUEST_BYTES = 12 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_KEY_FIELDS = frozenset(
    {
        "session_id",
        "pair_id",
        "audience_id",
        "epoch",
        "consent_version",
        "key_id",
        "public_key_b64",
        "expires_at_ms",
    }
)


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/keys")
@check_user_auth
def register_speech_evidence_key():
    try:
        body = _body(_KEY_FIELDS, required=_KEY_FIELDS)
        record, created = _service().register_key(
            _principal(),
            session_id=_identifier(body["session_id"]),
            pair_id=_identifier(body["pair_id"]),
            audience_id=_identifier(body["audience_id"]),
            epoch=_integer(body["epoch"], minimum=1),
            consent_version=_integer(body["consent_version"], minimum=1),
            key_id=_identifier(body["key_id"]),
            public_key_b64=_bounded_string(body["public_key_b64"], maximum=64),
            expires_at_ms=_integer(body["expires_at_ms"], minimum=1),
        )
        return _ok({"key": _public_key(record)}, status_code=201 if created else 200)
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.get("/v1/voice/speech-evidence-sync/keys/<key_id>")
@check_user_auth
def discover_speech_evidence_key(key_id: str):
    try:
        _closed_query({"session_id", "pair_id", "sender_id", "epoch"})
        record = _service().discover_key(
            _principal(),
            session_id=_identifier(request.args.get("session_id")),
            pair_id=_identifier(request.args.get("pair_id")),
            sender_id=_identifier(request.args.get("sender_id")),
            epoch=_integer(request.args.get("epoch"), minimum=1, allow_string=True),
            key_id=_identifier(key_id),
        )
        return _ok({"key": _public_key(record)})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.get("/v1/voice/speech-evidence-sync/consents/current")
@check_user_auth
def current_speech_evidence_consent_pair():
    try:
        _closed_query({"session_id", "pair_id", "remote_peer_id", "epoch"})
        local, remote = _service().current_consent_pair(
            _principal(),
            session_id=_identifier(request.args.get("session_id")),
            pair_id=_identifier(request.args.get("pair_id")),
            remote_peer_id=_identifier(request.args.get("remote_peer_id")),
            epoch=_integer(request.args.get("epoch"), minimum=1, allow_string=True),
        )
        return _ok({"local": _public_consent(local), "remote": _public_consent(remote)})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/offers/proposals")
@check_user_auth
def propose_speech_evidence_offer():
    return _signed_offer("proposal")


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/offers/acceptances")
@check_user_auth
def accept_speech_evidence_offer():
    return _signed_offer("acceptance")


@speech_evidence_sync_bp.get("/v1/voice/speech-evidence-sync/offers")
@check_user_auth
def list_speech_evidence_offers():
    try:
        _closed_query({"session_id", "pair_id", "epoch"})
        offers = _service().list_offers(
            _principal(),
            session_id=_identifier(request.args.get("session_id")),
            pair_id=_identifier(request.args.get("pair_id")),
            epoch=_integer(request.args.get("epoch"), minimum=1, allow_string=True),
        )
        return _ok({"offers": [offer_public_dict(offer) for offer in offers]})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/offers/<offer_id>/authorize-transfer")
@check_user_auth
def authorize_speech_evidence_transfer(offer_id: str):
    try:
        _empty_body()
        offer = _service().authorize_transfer(_principal(), _identifier(offer_id))
        return _ok({"offer": offer_public_dict(offer)})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/transfers/chunks")
@check_user_auth
def append_speech_evidence_chunk():
    try:
        fields = frozenset({"message", "relay_envelope"})
        body = _body(fields, required=fields)
        transfer, relay = _service().append_chunk(
            _principal(),
            _message(body["message"]),
            _message(body["relay_envelope"]),
        )
        return _ok(
            {
                "transfer": transfer.public_dict(),
                "relay": {name: relay[name] for name in ("message_id", "cursor", "expires_at_ms") if name in relay},
            },
            status_code=202,
        )
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/transfers/acks")
@check_user_auth
def acknowledge_speech_evidence_chunk():
    try:
        body = _body(
            frozenset({"message", "relay_envelope"}),
            required=frozenset({"message"}),
        )
        relay_envelope = body.get("relay_envelope")
        transfer = _service().acknowledge_chunk(
            _principal(),
            _message(body["message"]),
            _message(relay_envelope) if relay_envelope is not None else None,
        )
        return _ok({"transfer": transfer.public_dict()})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.get("/v1/voice/speech-evidence-sync/offers/<offer_id>/transfers/<group_id>")
@check_user_auth
def get_speech_evidence_transfer(offer_id: str, group_id: str):
    try:
        _closed_query(set())
        transfer = _service().transfer_status(
            _principal(),
            offer_id=_identifier(offer_id),
            group_id=_identifier(group_id),
        )
        return _ok({"transfer": transfer.public_dict()})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/offers/<offer_id>/curation")
@check_user_auth
def request_speech_evidence_curation(offer_id: str):
    """Disclose ACK-bound clear chunks for a separate Hub admission decision."""

    try:
        body = _curation_body()
        message = _message(body["message"])
        payload = message.get("payload")
        if not isinstance(payload, Mapping) or payload.get("offer_id") != _identifier(offer_id):
            raise HubSpeechEvidenceSyncError("speech_evidence_curation_offer_mismatch", status_code=409)
        groups = body["groups"]
        if not isinstance(groups, list):
            raise HubSpeechEvidenceSyncError("speech_evidence_curation_groups_invalid", status_code=400)
        record, created = _curation_service().request(
            _principal(),
            signed_message=message,
            groups=groups,
        )
        service = _curation_service()
        return _ok(
            {
                "curation": record.public_dict(),
                "hub_receipt_key": service.receipt_public_key(),
            },
            status_code=201 if created else 200,
        )
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.get("/v1/voice/speech-evidence-sync/offers/<offer_id>/curation")
@check_user_auth
def get_speech_evidence_curation(offer_id: str):
    try:
        _closed_query(set())
        record = _curation_service().get(_principal(), _identifier(offer_id))
        return _ok(
            {
                "curation": record.public_dict(),
                "hub_receipt_key": _curation_service().receipt_public_key(),
            }
        )
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/internal/v1/voice/speech-evidence-curation/tasks/<task_id>/claim")
@check_service_auth(scope=SPEECH_EVIDENCE_CURATION_WORKER_SCOPE)
def claim_speech_evidence_curation_task(task_id: str):
    try:
        _empty_body()
        worker_id, worker_url = _worker_identity()
        value = _curation_service().claim_input(
            task_id=_identifier(task_id),
            executor_id=worker_id,
            executor_url=worker_url,
        )
        return _ok(value)
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/internal/v1/voice/speech-evidence-curation/results")
@check_service_auth(scope=SPEECH_EVIDENCE_CURATION_WORKER_SCOPE)
def admit_speech_evidence_curation_result():
    try:
        body = _worker_body()
        worker_id, _worker_url = _worker_identity()
        record = _curation_service().admit_result(
            executor_id=worker_id,
            result_raw=body["result"],
            artifact_raw=body["artifact"],
        )
        return _ok({"curation": record.public_dict()})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


@speech_evidence_sync_bp.post("/v1/voice/speech-evidence-sync/offers/<offer_id>/invalidate")
@check_user_auth
def invalidate_speech_evidence_offer(offer_id: str):
    try:
        body = _body(frozenset({"reason_code"}), required=frozenset({"reason_code"}))
        reason_code = _identifier(body["reason_code"])
        principal = _principal()
        normalized_offer_id = _identifier(offer_id)
        service = _service()
        service.authorize_offer_access(principal, normalized_offer_id)
        _fence_curation_if_present(principal, normalized_offer_id, reason_code)
        offer = service.invalidate(
            principal,
            offer_id=normalized_offer_id,
            reason_code=reason_code,
        )
        # The pre-fence handles existing descendants. The post-fence closes
        # the authorize-vs-invalidate race: a curation holding the canonical
        # Offer guard must publish its projection before invalidation can
        # commit, so this second idempotent pass observes and fences it.
        _fence_curation_if_present(principal, normalized_offer_id, reason_code)
        return _ok({"offer": offer_public_dict(offer)})
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


def _signed_offer(stage: str):
    try:
        body = _body(
            frozenset({"message", "relay_envelope"}),
            required=frozenset({"message"}),
        )
        callback = _service().propose if stage == "proposal" else _service().accept
        relay_envelope = body.get("relay_envelope")
        offer = callback(
            _principal(),
            _message(body["message"]),
            _message(relay_envelope) if relay_envelope is not None else None,
        )
        return _ok({"offer": offer_public_dict(offer)}, status_code=201 if stage == "proposal" else 200)
    except Exception as exc:
        return _error(speech_evidence_sync_error(exc))


def _service() -> HubSpeechEvidenceSyncService:
    flags = current_app.extensions.get("semantic_media_feature_flags")
    if not isinstance(flags, Mapping) or flags.get("peer_evidence_sync") is not True:
        raise HubSpeechEvidenceSyncError("semantic_feature_disabled", status_code=403)
    service = current_app.extensions.get("speech_evidence_sync_service")
    if not isinstance(service, HubSpeechEvidenceSyncService):
        raise HubSpeechEvidenceSyncError("speech_evidence_sync_unavailable", status_code=503)
    return service


def _curation_service() -> SpeechPeerEvidenceCurationService:
    # The same feature gate protects sync and its additive curation boundary.
    _service()
    service = current_app.extensions.get("speech_peer_evidence_curation_service")
    if not isinstance(service, SpeechPeerEvidenceCurationService):
        raise SpeechPeerCurationError("speech_peer_curation_unavailable", status_code=503)
    return service


def _fence_curation_if_present(principal: VoicePrincipal, offer_id: str, reason_code: str) -> None:
    service = current_app.extensions.get("speech_peer_evidence_curation_service")
    if isinstance(service, SpeechPeerEvidenceCurationService):
        service.fence_offer(principal, offer_id=offer_id, reason_code=reason_code)
        return
    # A sync-only installation can invalidate an offer that never entered
    # curation. Persisted descendants without their fencing service are a hard
    # failure; claiming offer cleanup alone would be misleading and unsafe.
    with Session(engine) as session:
        existing = session.exec(
            select(SpeechPeerEvidenceCurationDB.id).where(
                SpeechPeerEvidenceCurationDB.tenant_id == principal.tenant_id,
                SpeechPeerEvidenceCurationDB.offer_id == offer_id,
            )
        ).first()
    if existing is not None:
        raise SpeechPeerCurationError("speech_peer_curation_unavailable", status_code=503)


def _principal() -> VoicePrincipal:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or "").strip()
    tenant_id = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    if not subject or not tenant_id:
        raise HubSpeechEvidenceSyncError("speech_evidence_unauthenticated", status_code=401)
    try:
        return VoicePrincipal(tenant_id, subject)
    except VoiceGovernanceError as exc:
        raise HubSpeechEvidenceSyncError("speech_evidence_unauthenticated", status_code=401) from exc


def _body(allowed: frozenset[str], *, required: frozenset[str]) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_REQUEST_BYTES:
        raise HubSpeechEvidenceSyncError("speech_evidence_message_oversized", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise HubSpeechEvidenceSyncError("speech_evidence_json_invalid", status_code=400)
    if set(value) - allowed:
        raise HubSpeechEvidenceSyncError("speech_evidence_unknown_field", status_code=400)
    if required - set(value):
        raise HubSpeechEvidenceSyncError("speech_evidence_required_field_missing", status_code=400)
    return dict(value)


def _curation_body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > _MAX_CURATION_REQUEST_BYTES:
        raise HubSpeechEvidenceSyncError("speech_evidence_curation_request_oversized", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping) or set(value) != {"message", "groups"}:
        raise HubSpeechEvidenceSyncError("speech_evidence_curation_json_invalid", status_code=400)
    return dict(value)


def _worker_body() -> dict[str, Any]:
    if request.content_length is not None and request.content_length > 512 * 1024:
        raise HubSpeechEvidenceSyncError("speech_evidence_curation_result_oversized", status_code=413)
    value = request.get_json(silent=True)
    if not isinstance(value, Mapping) or set(value) != {"result", "artifact"}:
        raise HubSpeechEvidenceSyncError("speech_evidence_curation_result_invalid", status_code=400)
    return dict(value)


def _worker_identity() -> tuple[str, str]:
    identity = dict(getattr(g, "service_identity", {}) or {})
    worker_id = _identifier(identity.get("worker_id"))
    worker_url = str(identity.get("worker_url") or "").strip().rstrip("/")
    if not worker_url or len(worker_url) > 512:
        raise HubSpeechEvidenceSyncError("speech_evidence_curation_worker_identity_invalid", status_code=403)
    return worker_id, worker_url


def _empty_body() -> None:
    if request.content_length in {None, 0}:
        return
    value = request.get_json(silent=True)
    if value not in ({}, None):
        raise HubSpeechEvidenceSyncError("speech_evidence_unknown_field", status_code=400)


def _message(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise HubSpeechEvidenceSyncError("speech_evidence_message_invalid", status_code=400)
    return dict(value)


def _identifier(value: object) -> str:
    rendered = value.strip() if isinstance(value, str) else ""
    if _ID.fullmatch(rendered) is None:
        raise HubSpeechEvidenceSyncError("speech_evidence_identifier_invalid", status_code=400)
    return rendered


def _integer(value: object, *, minimum: int, allow_string: bool = False) -> int:
    if type(value) is not int and allow_string:
        try:
            value = int(str(value))
        except (TypeError, ValueError) as exc:
            raise HubSpeechEvidenceSyncError("speech_evidence_integer_invalid", status_code=400) from exc
    if type(value) is not int or not minimum <= value <= 9_007_199_254_740_991:
        raise HubSpeechEvidenceSyncError("speech_evidence_integer_invalid", status_code=400)
    return value


def _bounded_string(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise HubSpeechEvidenceSyncError("speech_evidence_value_invalid", status_code=400)
    return value


def _closed_query(allowed: set[str]) -> None:
    if set(request.args) != allowed:
        raise HubSpeechEvidenceSyncError("speech_evidence_query_invalid", status_code=400)


def _public_key(record) -> dict[str, object]:
    return {
        "session_id": record.session_id,
        "pair_id": record.pair_id,
        "sender_id": record.sender_id,
        "audience_id": record.audience_id,
        "epoch": record.epoch,
        "key_id": record.key_id,
        "public_key_b64": record.public_key_b64,
        "fingerprint": record.fingerprint,
        "consent_version": record.consent_version,
        "expires_at_ms": record.expires_at_ms,
        "version": record.version,
    }


def _public_consent(record) -> dict[str, object]:
    return {
        "peer_id": record.peer_id,
        "pair_id": record.pair_id,
        "version": record.version,
        "digest": record.digest,
        "directions": sorted(record.directions),
        "purposes": sorted(record.purposes),
        "data_classes": sorted(record.data_classes),
        "fields": sorted(record.fields),
        "trainer_classes": sorted(record.trainer_classes),
        "maximum_retention_seconds": record.maximum_retention_seconds,
        "expires_at_ms": record.expires_at_ms,
    }


def _ok(data: dict[str, object], *, status_code: int = 200):
    return jsonify({"ok": True, "data": data}), status_code


def _error(exc: HubSpeechEvidenceSyncError):
    return jsonify(
        {
            "ok": False,
            "error": {
                "code": exc.reason_code,
                "retriable": exc.status_code >= 500 or exc.status_code == 429,
            },
        }
    ), exc.status_code


__all__ = ["speech_evidence_sync_bp"]
