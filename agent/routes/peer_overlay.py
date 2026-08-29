"""Authenticated Hub API for signed peer-overlay membership and routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.errors import api_response
from agent.services.peer_overlay_control_service import PeerOverlayDenied
from agent.services.peer_overlay_state_store import PeerOverlayStateConflict

peer_overlay_bp = Blueprint("peer_overlay", __name__, url_prefix="/api/peer-overlay")


def _service():
    value = current_app.extensions.get("peer_overlay_control_service")
    if value is None:
        raise RuntimeError("peer_overlay_unavailable")
    return value


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("peer_overlay_payload_invalid")
    return value


def _invoke(operation: Callable[..., dict[str, Any]], payload: dict[str, Any] | None = None):
    try:
        return api_response(data=operation(**(payload if payload is not None else _payload())))
    except PeerOverlayStateConflict as exc:
        return api_response(status="error", message=str(exc), code=409)
    except PeerOverlayDenied as exc:
        return api_response(status="error", message=str(exc), code=403)
    except KeyError as exc:
        return api_response(status="error", message=str(exc.args[0]), code=404)
    except (TypeError, ValueError) as exc:
        return api_response(status="error", message=str(exc), code=422)
    except RuntimeError as exc:
        return api_response(status="error", message=str(exc), code=503)


@peer_overlay_bp.get("/overview")
@check_auth
def overview():
    return _invoke(
        _service().overview,
        {
            "tenant_id": str(request.args.get("tenant_id") or "").strip() or None,
            "room_id": str(request.args.get("room_id") or "").strip() or None,
        },
    )


@peer_overlay_bp.post("/memberships")
@admin_required
def change_membership():
    return _invoke(_service().change_membership)


@peer_overlay_bp.post("/plans")
@admin_required
def plan_publication():
    return _invoke(_service().plan_publication)


@peer_overlay_bp.post("/tickets")
@admin_required
def issue_link_ticket():
    return _invoke(_service().issue_link_ticket)


@peer_overlay_bp.post("/tickets/consume")
@check_auth
def consume_link_ticket():
    return _invoke(_service().consume_link_ticket)


@peer_overlay_bp.post("/failovers")
@admin_required
def request_automatic_failover():
    return _invoke(_service().request_automatic_failover)


@peer_overlay_bp.post("/offline-authority")
@admin_required
def offline_authority():
    return _invoke(_service().offline_authority)


__all__ = ["peer_overlay_bp"]
