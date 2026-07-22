"""Authenticated browser boundary for bounded receiver-quality aggregates."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.sfu_receiver_quality_ingestion_service import (
    SfuReceiverQualityCommand,
    SfuReceiverQualityError,
    SfuReceiverQualityIngestionService,
)


webrtc_sfu_broadcast_quality_bp = Blueprint(
    "webrtc_sfu_broadcast_quality",
    __name__,
)


@webrtc_sfu_broadcast_quality_bp.post(
    "/v1/semantic-media/sfu/quality-observations/<subscription_ref>"
)
@check_user_auth
def ingest_receiver_quality(subscription_ref: str):
    try:
        command = _command(subscription_ref, include_body=True)
        result = _service().ingest(command)
    except SfuReceiverQualityError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "error": exc.reason_code, "reason_code": exc.reason_code}), exc.status_code
    _audit(result.status, result.reason_code)
    return jsonify(result.payload()), 200 if result.status == "dropped" else 202


@webrtc_sfu_broadcast_quality_bp.get(
    "/v1/semantic-media/sfu/quality-observations/<subscription_ref>"
)
@check_user_auth
def read_receiver_quality_window(subscription_ref: str):
    try:
        command = _command(subscription_ref, include_body=False)
        reports = _service().read_window(command)
    except SfuReceiverQualityError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "error": exc.reason_code, "reason_code": exc.reason_code}), exc.status_code
    return jsonify(
        {
            "ok": True,
            "authoritative": False,
            "authorization_effect": "none",
            "reports": list(reports),
        }
    )


@webrtc_sfu_broadcast_quality_bp.delete(
    "/v1/semantic-media/sfu/quality-observations/<subscription_ref>"
)
@check_user_auth
def clear_receiver_quality_subscription(subscription_ref: str):
    try:
        actor, tenant = _identity()
        session_id, _ = _query_scope()
        removed = _service().purge_subscription(
            tenant_id=tenant,
            session_id=session_id,
            subscriber_ref=actor,
            subscription_ref=subscription_ref,
        )
    except SfuReceiverQualityError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "error": exc.reason_code, "reason_code": exc.reason_code}), exc.status_code
    _audit("cleared", "quality_subscription_cleared")
    return jsonify({"ok": True, "reason_code": "quality_subscription_cleared", "removed": removed})


@webrtc_sfu_broadcast_quality_bp.delete(
    "/v1/semantic-media/sfu/quality-observations"
)
@check_user_auth
def clear_receiver_quality_participant():
    try:
        actor, tenant = _identity()
        session_id, _ = _query_scope()
        removed = _service().purge_participant(
            tenant_id=tenant,
            session_id=session_id,
            subscriber_ref=actor,
        )
    except SfuReceiverQualityError as exc:
        _audit("denied", exc.reason_code)
        return jsonify({"ok": False, "error": exc.reason_code, "reason_code": exc.reason_code}), exc.status_code
    _audit("cleared", "quality_participant_left")
    return jsonify({"ok": True, "reason_code": "quality_participant_left", "removed": removed})


def _command(subscription_ref: str, *, include_body: bool) -> SfuReceiverQualityCommand:
    actor, tenant = _identity()
    session_id, membership_epoch = _query_scope()
    raw = request.get_data(cache=False, as_text=False) if include_body else b"{}"
    return SfuReceiverQualityCommand(
        raw_document=raw,
        actor_id=actor,
        tenant_id=tenant,
        session_id=session_id,
        membership_epoch=membership_epoch,
        subscription_ref=subscription_ref,
    )


def _identity() -> tuple[str, str]:
    auth = dict(get_request_auth_context() or {})
    actor = str(auth.get("sub") or auth.get("username") or "").strip()
    tenant = str(auth.get("tenant_id") or auth.get("tenant") or "default").strip()
    if not actor or not tenant:
        raise SfuReceiverQualityError("quality_identity_invalid", 401)
    return actor, tenant


def _query_scope() -> tuple[str, int]:
    session_id = str(request.args.get("session_id") or "")
    raw_epoch: Any = request.args.get("membership_epoch")
    try:
        membership_epoch = int(str(raw_epoch))
    except (TypeError, ValueError) as exc:
        raise SfuReceiverQualityError("quality_membership_epoch_invalid") from exc
    if not session_id or membership_epoch < 1:
        raise SfuReceiverQualityError("quality_scope_invalid")
    return session_id, membership_epoch


def _service() -> SfuReceiverQualityIngestionService:
    service = current_app.extensions.get("sfu_receiver_quality_ingestion_service")
    if not isinstance(service, SfuReceiverQualityIngestionService):
        raise SfuReceiverQualityError("quality_ingestion_service_unavailable", 503)
    return service


def _audit(status: str, reason_code: str) -> None:
    log_audit(
        "sfu_receiver_quality_ingestion",
        {"status": status, "reason_code": reason_code, "authoritative": False},
    )


__all__ = ["webrtc_sfu_broadcast_quality_bp"]
