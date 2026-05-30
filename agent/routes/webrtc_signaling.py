"""PRD04.01: WebRTC Signaling-Endpunkte für wss://webrtc.ananta.de/signaling.

- An ShareSession und Participant gebunden
- SDP Offers/Answers nur zwischen berechtigten Teilnehmern
- ICE Candidates sessiongebunden
- Nachrichten größen- und zeitbegrenzt
- Fallback auf Hub Relay bei WebRTC-Failure
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_user_auth, get_request_auth_context
from agent.common.audit import log_audit
from agent.services.rate_limit_service import RateLimitService
from agent.services.share_session_service import get_share_session_service

webrtc_signaling_bp = Blueprint("webrtc_signaling", __name__)

_rate_limiter = RateLimitService()
_RATE_SIGNAL = {"namespace": "webrtc_signal", "limit": 30, "window_seconds": 10}

_MAX_SIGNAL_BYTES = 8 * 1024  # 8 KB pro Signal
_MAX_QUEUE_DEPTH = 20

_signal_queues: dict[str, list[dict[str, Any]]] = defaultdict(list)  # session_id:user_id -> signals


def _current_user_id() -> str:
    auth = dict(get_request_auth_context() or {})
    return str(auth.get("sub") or auth.get("username") or "").strip()


def _queue_key(session_id: str, recipient_user_id: str) -> str:
    return f"{session_id}:{recipient_user_id}"


def _is_authorized_participant(session_id: str, user_id: str) -> bool:
    service = get_share_session_service()
    session = service.get_session(session_id)
    if not session:
        return False
    if str(session.get("owner_user_id") or "") == user_id:
        return True
    participants = service.get_participants(session_id)
    return any(
        str(p.get("user_id") or "") == user_id and not p.get("revoked_at")
        for p in participants
    )


@webrtc_signaling_bp.route("/webrtc/sessions/<session_id>/signal", methods=["POST"])
@check_user_auth
def send_signal(session_id: str):
    """Sender schickt SDP Offer/Answer oder ICE Candidate."""
    sender_id = _current_user_id()
    if not sender_id:
        return jsonify({"error": "not_authenticated"}), 401
    if not _rate_limiter.allow_request(namespace=_RATE_SIGNAL["namespace"], subject=sender_id, limit=_RATE_SIGNAL["limit"], window_seconds=_RATE_SIGNAL["window_seconds"]):
        return jsonify({"error": "rate_limited"}), 429
    raw = request.get_data(as_text=False)
    if len(raw) > _MAX_SIGNAL_BYTES:
        return jsonify({"error": "signal_too_large"}), 413
    if not _is_authorized_participant(session_id, sender_id):
        return jsonify({"error": "forbidden"}), 403
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    recipient_id = str(body.get("recipient_id") or "").strip()
    if not recipient_id:
        return jsonify({"error": "recipient_id_required"}), 400
    if not _is_authorized_participant(session_id, recipient_id):
        return jsonify({"error": "recipient_not_authorized"}), 403
    signal_type = str(body.get("type") or "").strip()
    if signal_type not in {"offer", "answer", "ice_candidate"}:
        return jsonify({"error": "invalid_signal_type"}), 400
    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "type": signal_type,
        "payload": body.get("payload"),
        "sent_at": time.time(),
    }
    key = _queue_key(session_id, recipient_id)
    _signal_queues[key].append(entry)
    _signal_queues[key] = _signal_queues[key][-_MAX_QUEUE_DEPTH:]
    return jsonify({"ok": True, "signal_id": entry["id"]}), 201


@webrtc_signaling_bp.route("/webrtc/sessions/<session_id>/signal", methods=["GET"])
@check_user_auth
def poll_signals(session_id: str):
    """Empfänger holt anstehende Signals ab."""
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"error": "not_authenticated"}), 401
    if not _is_authorized_participant(session_id, user_id):
        return jsonify({"error": "forbidden"}), 403
    key = _queue_key(session_id, user_id)
    signals = list(_signal_queues.get(key) or [])
    _signal_queues[key] = []  # consume
    return jsonify({"ok": True, "data": {"signals": signals}}), 200
