"""mTLS-only ingress route for signed TURN observations."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Mapping

from flask import Blueprint, jsonify, request


TransportIdentityResolver = Callable[[Mapping[str, Any]], Any | None]
ObservationHandler = Callable[[bytes, Any], Any]
AuditLogger = Callable[[str, Mapping[str, Any]], None]


def build_turn_observation_blueprint(
    *,
    transport_identity_resolver: TransportIdentityResolver,
    observation_handler: ObservationHandler,
    audit_logger: AuditLogger,
    max_body_bytes: int = 65_536,
) -> Blueprint:
    """Resolve identity exclusively from trusted server transport metadata.

    The resolver receives the WSGI environment, never request headers.  A
    deployment must populate it from its TLS terminator or direct TLS socket.
    Bearer roles, worker IDs and caller-provided certificate headers are not an
    alternate authorization path.
    """

    if not 4_096 <= max_body_bytes <= 262_144:
        raise ValueError("turn_observation_body_limit_invalid")
    blueprint = Blueprint("webrtc_turn_observations", __name__)

    @blueprint.post("/api/webrtc/turn-observations")
    def ingest() -> Any:
        if not request.is_secure:
            return jsonify({"reason_code": "turn_observation_tls_required"}), 403
        if request.content_length is not None and request.content_length > max_body_bytes:
            return jsonify({"reason_code": "turn_observation_body_too_large"}), 413
        transport_identity = transport_identity_resolver(request.environ)
        if transport_identity is None:
            return jsonify({"reason_code": "turn_observation_mtls_identity_missing"}), 403
        raw_body = request.get_data(cache=False, as_text=False)
        if not raw_body or len(raw_body) > max_body_bytes:
            return jsonify({"reason_code": "turn_observation_body_invalid"}), 400
        try:
            result = observation_handler(raw_body, transport_identity)
        except Exception as exc:  # adapter exposes only normalized reason/status
            reason = getattr(exc, "reason_code", "turn_observation_rejected")
            status = int(getattr(exc, "status_code", 409))
            audit_logger("turn_observation_rejected", {"reason_code": reason})
            return jsonify({"reason_code": reason}), status
        response = asdict(result) if is_dataclass(result) else dict(result)
        audit_logger(
            "turn_observation_accepted",
            {
                "pool_id": response.get("pool_id"),
                "instance_id": response.get("instance_id"),
                "observation_id": response.get("observation_id"),
                "status": response.get("status"),
            },
        )
        return jsonify(response), 202 if response.get("status") == "accepted" else 200

    return blueprint

