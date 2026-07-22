"""Dependency-injected administrator routes for TURN observer identities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from functools import wraps
from typing import Any, Callable, Mapping

from flask import Blueprint, jsonify, request


class TurnObserverAdminRequestError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


AdminGuard = Callable[[Callable[..., Any]], Callable[..., Any]]
ActorResolver = Callable[[], str]
CommandHandler = Callable[[str, Mapping[str, Any], str, str], Any]
AuditLogger = Callable[[str, Mapping[str, Any]], None]


_FIELDS = {
    "enroll": frozenset(
        {
            "identity_id",
            "pool_id",
            "instance_id",
            "public_key",
            "proof_nonce",
            "proof_signature",
            "certificate_fingerprint_sha256",
            "expected_version",
        }
    ),
    "rotate": frozenset(
        {
            "public_key",
            "proof_nonce",
            "proof_signature",
            "certificate_fingerprint_sha256",
            "expected_version",
        }
    ),
    "revoke": frozenset({"expected_version", "reason_code"}),
}


def build_turn_observer_admin_blueprint(
    *,
    admin_guard: AdminGuard,
    actor_resolver: ActorResolver,
    command_handler: CommandHandler,
    audit_logger: AuditLogger,
) -> Blueprint:
    """Build routes without coupling domain services to Flask globals."""

    blueprint = Blueprint("webrtc_turn_observer_enrollment", __name__)

    def endpoint(operation: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(function)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if request.content_length is not None and request.content_length > 16_384:
                    return jsonify({"reason_code": "turn_observer_admin_body_too_large"}), 413
                idempotency_key = request.headers.get("Idempotency-Key", "").strip()
                if not idempotency_key or len(idempotency_key) > 128:
                    return jsonify({"reason_code": "turn_observer_idempotency_key_invalid"}), 400
                body = request.get_json(silent=True)
                if not isinstance(body, dict) or set(body) != _FIELDS[operation]:
                    return jsonify({"reason_code": "turn_observer_admin_body_invalid"}), 400
                if _contains_private_key(body):
                    return jsonify({"reason_code": "turn_observer_private_key_forbidden"}), 400
                effective_body = dict(body)
                if "identity_id" in kwargs:
                    effective_body["identity_id"] = kwargs["identity_id"]
                actor_id = actor_resolver()
                if not actor_id:
                    return jsonify({"reason_code": "turn_observer_admin_actor_missing"}), 403
                try:
                    result = command_handler(operation, effective_body, actor_id, idempotency_key)
                except Exception as exc:  # adapter maps domain errors without leaking details
                    reason = getattr(exc, "reason_code", "turn_observer_admin_command_failed")
                    status = int(getattr(exc, "status_code", 409))
                    audit_logger(
                        "turn_observer_admin_rejected",
                        {"operation": operation, "actor_id": actor_id, "reason_code": reason},
                    )
                    return jsonify({"reason_code": reason}), status
                response = asdict(result) if is_dataclass(result) else dict(result)
                audit_logger(
                    "turn_observer_admin_accepted",
                    {
                        "operation": operation,
                        "actor_id": actor_id,
                        "identity_id": response.get("identity_id"),
                        "version": response.get("version"),
                    },
                )
                return jsonify(response), 200

            return admin_guard(wrapped)

        return decorate

    @blueprint.post("/api/webrtc/turn-observers")
    @endpoint("enroll")
    def enroll() -> Any:
        raise AssertionError("decorator handles request")

    @blueprint.post("/api/webrtc/turn-observers/<identity_id>/rotation")
    @endpoint("rotate")
    def rotate(identity_id: str) -> Any:
        raise AssertionError("decorator handles request")

    @blueprint.post("/api/webrtc/turn-observers/<identity_id>/revocation")
    @endpoint("revoke")
    def revoke(identity_id: str) -> Any:
        raise AssertionError("decorator handles request")

    return blueprint


def _contains_private_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if "private" in normalized and "key" in normalized:
                return True
            if _contains_private_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_private_key(item) for item in value)
    return False
