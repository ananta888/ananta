"""Request helpers shared by the public rendezvous views.

:class:`RequestSupport` authenticates the bearer token with an injected
verifier, parses closed JSON bodies, reads the peer/membership headers and
applies the rate limits of an injected service and configuration. ``app``
builds one instance per Flask application and hands it to the session and
transport route classes (DIP): the views never reach for module-level
authentication or service globals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Response, jsonify, request
from oidc_auth import AuthContext

TokenVerifier = Callable[[str], AuthContext]

_MEMBER_FORBIDDEN_REASONS = frozenset(
    {
        "forbidden",
        "local_peer_id_required",
        "membership_capability_required",
        "membership_capability_invalid",
    }
)


class RequestSupport:
    """Authentication, body parsing, header selection and rate limiting for one app."""

    def __init__(self, *, verify_token: TokenVerifier, service: Any, config: Any, logger: logging.Logger) -> None:
        self._verify_token = verify_token
        self._service = service
        self._config = config
        self._log = logger

    # --- Authentication ---

    def require_auth(self) -> AuthContext | None:
        """Return the verified auth context, or ``None`` when the bearer token is rejected."""
        auth_header = request.headers.get("Authorization", "")
        try:
            return self._verify_token(auth_header)
        except ValueError as exc:
            self._log.debug("Auth failed: %s", exc)
            return None

    @staticmethod
    def auth_error(msg: str = "unauthorized", status: int = 401):
        return jsonify({"error": msg}), status

    # --- Request bodies and headers ---

    @staticmethod
    def closed_json_body(allowed_fields: set[str]):
        """Parse a JSON object and reject fields outside the endpoint contract."""
        body = request.get_json(force=False, silent=True)
        if not isinstance(body, dict):
            return None, (jsonify({"error": "json_object_required"}), 400)
        if any(not isinstance(key, str) or key not in allowed_fields for key in body):
            return None, (jsonify({"error": "request_fields_not_allowed"}), 400)
        return body, None

    @staticmethod
    def requested_peer_id() -> str:
        return str(request.headers.get("X-Ananta-Peer-Id") or "").strip()

    @staticmethod
    def requested_device_id() -> str:
        return str(request.headers.get("X-Ananta-Device-Id") or "").strip()

    @staticmethod
    def membership_capability() -> str:
        return str(request.headers.get("X-Ananta-Membership-Capability") or "").strip()

    def selected_peer_id(self, account_id: str) -> str:
        """Select a claimed peer; domain services authenticate it before use."""
        return self.requested_peer_id() or account_id

    # --- Responses ---

    @staticmethod
    def session_for_local_peer(
        session: Any,
        peer_id: str,
        *,
        role: str = "",
        runtime_state: str = "",
    ) -> Any:
        if not isinstance(session, dict):
            return session
        projected = {
            **{key: value for key, value in session.items() if not key.startswith("_")},
            "local_peer_id": peer_id,
        }
        if role:
            projected["local_role"] = role
        if runtime_state:
            projected["local_runtime_state"] = runtime_state
        return projected

    @staticmethod
    def member_error_status(reason: str, *, default: int = 409) -> int:
        if reason in _MEMBER_FORBIDDEN_REASONS:
            return 403
        if reason == "session_not_found":
            return 404
        return default

    # --- Rate limiting ---

    def rate_limit_guard(self, namespace: str, subject: str, limit: int, window: int) -> tuple[Response, int] | None:
        """Return a standards-compatible 429 response, or ``None`` when allowed."""
        allowed, retry_after = self._service.rate_check_with_retry(namespace, subject, limit, window)
        if allowed:
            return None
        response = jsonify({"error": "rate_limited"})
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response, 429

    def recovery_probe_limit(self, account_id: str) -> tuple[Response, int] | None:
        """Bound idempotency lookups to the authenticated account."""
        return self.rate_limit_guard(
            "recovery_probe",
            account_id,
            self._config.RATE_RECOVERY_PROBE_LIMIT,
            self._config.RATE_RECOVERY_PROBE_WINDOW,
        )

    def membership_probe_limit(self, account_id: str) -> tuple[Response, int] | None:
        """Bound membership resolution before trusting a client peer selector."""
        return self.rate_limit_guard(
            "membership_probe",
            account_id,
            self._config.RATE_MEMBERSHIP_PROBE_LIMIT,
            self._config.RATE_MEMBERSHIP_PROBE_WINDOW,
        )


__all__ = ["RequestSupport", "TokenVerifier"]
