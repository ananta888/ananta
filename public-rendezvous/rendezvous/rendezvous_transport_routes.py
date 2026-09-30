"""TURN-credential and WebRTC signaling endpoints of the public rendezvous app.

``app.create_app`` owns the Flask application; it registers these views
through :meth:`TransportRoutes.register` and injects the request helpers
(``RequestSupport``), the rendezvous service and the configuration
(SRP/DIP). View function names - and therefore Flask endpoint
names and URLs - are unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from flask import Flask, jsonify, request

TURN_CREDENTIAL_ERROR_STATUS = {
    "session_not_found": 404,
    "forbidden": 403,
    "turn_not_configured": 503,
}


class TransportRouteSupport(Protocol):
    """Request helpers the transport views depend on (``RequestSupport`` in ``app``)."""

    def require_auth(self) -> Any: ...

    def auth_error(self, msg: str = ..., status: int = ...) -> Any: ...

    def closed_json_body(self, allowed_fields: set[str]) -> tuple[Any, Any]: ...

    def requested_peer_id(self) -> str: ...

    def membership_capability(self) -> str: ...

    def membership_probe_limit(self, account_id: str) -> Any: ...

    def rate_limit_guard(self, namespace: str, subject: str, limit: int, window: int) -> Any: ...

    def member_error_status(self, reason: str, *, default: int = ...) -> int: ...


class TransportRoutes:
    """TURN-credential and signaling views bound to their collaborators."""

    def __init__(self, *, support: TransportRouteSupport, service: Any, config: Any) -> None:
        self._support = support
        self._service = service
        self._config = config

    def register(self, app: Flask) -> None:
        """Add the views with their original URLs, methods and endpoint names."""
        app.add_url_rule(
            "/rendezvous/turn-credentials",
            endpoint="turn_credentials",
            view_func=self.turn_credentials,
            methods=["GET"],
        )
        app.add_url_rule(
            "/webrtc/sessions/<session_id>/signal",
            endpoint="push_signal",
            view_func=self.push_signal,
            methods=["POST"],
        )
        app.add_url_rule(
            "/webrtc/sessions/<session_id>/signal",
            endpoint="poll_signals",
            view_func=self.poll_signals,
            methods=["GET"],
        )
        app.add_url_rule(
            "/signaling",
            endpoint="signaling_alias",
            view_func=self.signaling_alias,
            methods=["GET", "POST"],
        )

    def turn_credentials(self):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        if set(request.args) != {"session_id"} or len(request.args.getlist("session_id")) != 1:
            error = "session_id_required" if "session_id" not in request.args else "turn_request_invalid"
            return jsonify({"error": error}), 400
        raw_session_id = str(request.args.get("session_id") or "").strip()
        try:
            session_id = str(uuid.UUID(raw_session_id))
        except (AttributeError, ValueError):
            return jsonify({"error": "session_id_invalid"}), 400
        if limited := self._support.membership_probe_limit(ctx.account_id):
            return limited
        requested_peer_id = self._support.requested_peer_id()
        membership_capability = self._support.membership_capability()
        membership = self._service.authenticate_session_membership(
            session_id=session_id,
            account_id=ctx.account_id,
            requested_peer_id=requested_peer_id,
            membership_capability=membership_capability,
        )
        if not membership.get("ok"):
            reason = str(membership.get("reason") or "forbidden")
            return jsonify({"error": reason}), self._support.member_error_status(
                reason,
                default=TURN_CREDENTIAL_ERROR_STATUS.get(reason, 409),
            )
        if limited := self._support.rate_limit_guard(
            "turn_credentials",
            # A retired session must not transfer its exhausted TURN budget to a
            # replacement session on the same device. Both values are canonical
            # server-resolved identifiers; the raw tuple is hashed by the limiter
            # and never contains the membership capability or bearer token.
            f"{session_id}\0{membership['local_peer_id']}",
            self._config.RATE_TURN_CREDENTIAL_LIMIT,
            self._config.RATE_TURN_CREDENTIAL_WINDOW,
        ):
            return limited
        result = self._service.issue_turn_credentials(
            session_id=session_id,
            requester_user_id=ctx.account_id,
            requester_peer_id=requested_peer_id,
            membership_capability=membership_capability,
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "turn_credentials_unavailable")
            status = self._support.member_error_status(
                reason,
                default=TURN_CREDENTIAL_ERROR_STATUS.get(reason, 409),
            )
            return jsonify({"error": reason}), status
        response = jsonify(
            {
                "ok": True,
                "session_id": session_id,
                "local_peer_id": result["credentials"]["local_peer_id"],
                "data": result["credentials"],
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response, 200

    def push_signal(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        raw = request.get_data(as_text=False)
        if len(raw) > self._service.MAX_SIGNAL_BYTES:
            return jsonify({"error": "signal_too_large"}), 413
        body, body_error = self._support.closed_json_body(
            {
                "type",
                "session_id",
                "sender_id",
                "recipient_id",
                "payload",
                "security_epoch",
            }
        )
        if body_error:
            return body_error
        assert body is not None
        declared_session = str(body.get("session_id") or "").strip()
        declared_sender = str(body.get("sender_id") or "").strip()
        if limited := self._support.membership_probe_limit(ctx.account_id):
            return limited
        requested_peer_id = self._support.requested_peer_id()
        membership_capability = self._support.membership_capability()
        membership = self._service.authenticate_session_membership(
            session_id=session_id,
            account_id=ctx.account_id,
            requested_peer_id=requested_peer_id,
            membership_capability=membership_capability,
            require_pair=True,
        )
        if not membership.get("ok"):
            reason = str(membership.get("reason") or "forbidden")
            return jsonify({"error": reason}), self._support.member_error_status(reason)
        selected_peer_id = str(membership["local_peer_id"])
        if declared_session and declared_session != session_id:
            return jsonify({"error": "signal_session_mismatch"}), 400
        if declared_sender and declared_sender != selected_peer_id:
            return jsonify({"error": "signal_sender_mismatch"}), 403
        recipient_id = str(body.get("recipient_id") or "").strip()
        if not recipient_id:
            return jsonify({"error": "recipient_id_required"}), 400
        if limited := self._support.rate_limit_guard(
            "signal", selected_peer_id, self._config.RATE_SIGNAL_LIMIT, self._config.RATE_SIGNAL_WINDOW,
        ):
            return limited
        signal_type = str(body.get("type") or "").strip()
        security_epoch = body.get("security_epoch")
        if security_epoch is not None and (
            isinstance(security_epoch, bool) or not isinstance(security_epoch, int) or security_epoch < 1
        ):
            return jsonify({"error": "signal_epoch_invalid"}), 400
        result = self._service.push_signal(
            session_id=session_id,
            sender_id=selected_peer_id,
            recipient_id=recipient_id,
            signal_type=signal_type,
            payload=body.get("payload"),
            security_epoch=security_epoch,
            sender_account_id=ctx.account_id,
            membership_capability=membership_capability,
        )
        if not result.get("ok"):
            reason = result["reason"]
            status = (
                self._support.member_error_status(reason)
                if reason
                in {
                    "forbidden",
                    "local_peer_id_required",
                    "membership_capability_required",
                    "membership_capability_invalid",
                }
                else 400
                if reason.startswith("invalid_signal")
                else 409
            )
            return jsonify({"error": reason}), status
        return jsonify({**result, "local_peer_id": selected_peer_id}), 201

    def poll_signals(self, session_id: str):
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        since_values = request.args.getlist("since")
        if len(since_values) > 1:
            return jsonify({"error": "signal_cursor_invalid"}), 400
        raw_since = str(since_values[0]) if since_values else ""
        if raw_since and (len(raw_since) > 19 or not raw_since.isascii() or not raw_since.isdecimal()):
            return jsonify({"error": "signal_cursor_invalid"}), 400
        since = int(raw_since) if raw_since else 0
        if since > self._service.MAX_SIGNAL_CURSOR:
            return jsonify({"error": "signal_cursor_invalid"}), 400
        epoch_values = request.args.getlist("security_epoch")
        if len(epoch_values) > 1:
            return jsonify({"error": "signal_epoch_invalid"}), 400
        raw_epoch = str(epoch_values[0]) if epoch_values else ""
        if raw_epoch and (
            len(raw_epoch) > 19 or not raw_epoch.isascii() or not raw_epoch.isdecimal() or int(raw_epoch) < 1
        ):
            return jsonify({"error": "signal_epoch_invalid"}), 400
        security_epoch = int(raw_epoch) if raw_epoch else None
        if limited := self._support.membership_probe_limit(ctx.account_id):
            return limited
        requested_peer_id = self._support.requested_peer_id()
        membership_capability = self._support.membership_capability()
        membership = self._service.authenticate_session_membership(
            session_id=session_id,
            account_id=ctx.account_id,
            requested_peer_id=requested_peer_id,
            membership_capability=membership_capability,
            require_pair=True,
        )
        if not membership.get("ok"):
            reason = str(membership.get("reason") or "forbidden")
            return jsonify({"error": reason}), self._support.member_error_status(reason)
        if limited := self._support.rate_limit_guard(
            "signal_poll",
            str(membership["local_peer_id"]),
            self._config.RATE_SIGNAL_POLL_LIMIT,
            self._config.RATE_SIGNAL_POLL_WINDOW,
        ):
            return limited
        result = self._service.poll_signals(
            session_id=session_id,
            user_id=ctx.account_id,
            since=since,
            requester_peer_id=requested_peer_id,
            membership_capability=membership_capability,
            security_epoch=security_epoch,
        )
        if not result.get("ok"):
            reason = str(result.get("reason") or "forbidden")
            status = 400 if reason == "signal_cursor_invalid" else self._support.member_error_status(reason)
            return jsonify({"error": reason}), status
        data = {key: value for key, value in result.items() if key != "ok"}
        return jsonify(
            {
                "ok": True,
                "local_peer_id": result["local_peer_id"],
                "data": data,
            }
        ), 200

    def signaling_alias(self):
        """HTTP-Polling-Kompatibilitäts-Endpunkt. Leitet zu /webrtc/sessions/<id>/signal."""
        ctx = self._support.require_auth()
        if not ctx:
            return self._support.auth_error()
        session_id = str(request.args.get("session_id") or "").strip()
        if not session_id:
            return jsonify({"error": "session_id query param required"}), 400
        if request.method == "POST":
            return self.push_signal(session_id)
        return self.poll_signals(session_id)


__all__ = ["TURN_CREDENTIAL_ERROR_STATUS", "TransportRouteSupport", "TransportRoutes"]
