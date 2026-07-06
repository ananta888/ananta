"""Classroom-Routen (track classroom-transcript-codecompass-assistant).

Webhook nach dem Muster von agent/routes/webhooks.py (Secret-Allowlist,
HMAC-Signatur, kein Gateway-Aufruf vor bestandener Pruefung); Cards-API
fuer das Dozenten-Dashboard; Export streng read-only (CTA-009).
"""
from __future__ import annotations

import hashlib
import hmac
import json

from flask import Blueprint, current_app, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.classroom import privacy_policy
from agent.services.classroom.classroom_event_gateway import get_classroom_event_gateway
from agent.services.classroom.teacher_action_card_service import get_teacher_action_card_service

classroom_bp = Blueprint("classroom", __name__)

_SIGNATURE_HEADER = "X-Classroom-Signature"


def _classroom_config() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    block = cfg.get("classroom")
    return block if isinstance(block, dict) else {}


def _verify_signature(payload_raw: bytes, secret: str) -> bool:
    provided = str(request.headers.get(_SIGNATURE_HEADER) or "").strip()
    if not provided:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


@classroom_bp.route("/webhook/<source>", methods=["POST"])
def classroom_webhook(source: str):
    config = _classroom_config()
    secrets = config.get("webhook_secrets") if isinstance(config.get("webhook_secrets"), dict) else {}
    normalized_source = str(source or "").strip().lower()
    secret = str(secrets.get(normalized_source) or "").strip()
    if not secret:
        return api_response(status="error", message="unknown_webhook_source", code=403)

    payload_raw = request.get_data() or b""
    if not _verify_signature(payload_raw, secret):
        # Kein Gateway-Aufruf bei ungueltiger Signatur (Acceptance CTA-001).
        return api_response(status="error", message="invalid_signature", code=401)

    try:
        payload = json.loads(payload_raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return api_response(status="error", message="invalid_json", code=400)

    result = get_classroom_event_gateway().process_event(payload, source_adapter="webhook")
    code = 200 if result.get("status") != "error" else 422
    return api_response(data=result, code=code)


@classroom_bp.route("/cards", methods=["GET"])
@check_auth
def list_cards():
    cards = get_teacher_action_card_service().list_cards(
        zoom_room=str(request.args.get("zoom_room") or "").strip() or None,
        module=str(request.args.get("module") or "").strip() or None,
        status=str(request.args.get("status") or "").strip() or None,
    )
    return api_response(data={"items": cards, "count": len(cards)})


@classroom_bp.route("/cards/<card_id>", methods=["GET"])
@check_auth
def get_card(card_id: str):
    card = get_teacher_action_card_service().get_card(card_id)
    if card is None:
        return api_response(status="error", message="card_not_found", code=404)
    return api_response(data=card)


@classroom_bp.route("/cards/<card_id>/status", methods=["POST"])
@check_auth
def update_card_status(card_id: str):
    body = request.get_json(silent=True) or {}
    try:
        card = get_teacher_action_card_service().update_status(card_id, str(body.get("status") or ""))
    except KeyError:
        return api_response(status="error", message="card_not_found", code=404)
    except ValueError as exc:
        return api_response(status="error", message=str(exc), code=400)
    return api_response(data=card)


@classroom_bp.route("/cards/<card_id>/export", methods=["POST"])
@check_auth
def export_workflow_part(card_id: str):
    """CTA-009: read-only Export. failed-Teile sind nicht exportierbar;
    warning nur mit Banner. Es findet KEINE Einspielung statt."""
    card = get_teacher_action_card_service().get_card(card_id)
    if card is None:
        return api_response(status="error", message="card_not_found", code=404)
    workflow_part = card.get("workflow_part")
    if not workflow_part:
        return api_response(status="error", message="no_workflow_part", code=404)
    verifier_status = str(workflow_part.get("verifier_status") or "")
    if verifier_status == "failed":
        return api_response(status="error", message="workflow_verification_failed_export_blocked", code=409)

    export_payload = {
        "reviewed_by_verifier": verifier_status,
        "import_hint": workflow_part.get("import_hint"),
        "source_ref": workflow_part.get("source_ref"),
        "workflow": workflow_part.get("part"),
    }
    if verifier_status == "warning":
        export_payload["warning_banner"] = workflow_part.get("verifier_reasons")
    log_audit(privacy_policy.AUDIT_WORKFLOW_EXPORTED, {
        "card_id": card_id,
        "verifier_status": verifier_status,
        "form": workflow_part.get("form"),
    })
    return api_response(data=export_payload)
