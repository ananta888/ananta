"""Internal machine boundary for authenticated SFU observations."""

from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.services.sfu_node_observation_ingestion_service import (
    SfuNodeObservationAuthentication,
    SfuNodeObservationError,
    SfuNodeObservationIngestionService,
    authenticate_collector_token,
)


webrtc_sfu_node_observations_bp = Blueprint(
    "webrtc_sfu_node_observations",
    __name__,
)


@webrtc_sfu_node_observations_bp.post("/api/internal/webrtc/sfu-node-observations")
def ingest_sfu_node_observation():
    raw = request.get_data(cache=False, as_text=False)
    try:
        result = _service().ingest(raw, _authentication())
    except SfuNodeObservationError as exc:
        log_audit(
            "sfu_node_observation_ingestion",
            {"status": "denied", "reason_code": exc.reason_code},
        )
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code},
            code=exc.status_code,
        )
    log_audit(
        "sfu_node_observation_ingestion",
        {
            "status": result.status,
            "observation_id": result.observation_id,
            "node_id": None if result.node is None else result.node.node_id,
        },
    )
    return api_response(
        data=result.payload(),
        code=200 if result.status == "duplicate" else 202,
    )


def _authentication() -> SfuNodeObservationAuthentication:
    authorization = str(request.headers.get("Authorization") or "")
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    expected_digest = current_app.extensions.get(
        "sfu_node_observation_collector_token_digest"
    )
    collector_authenticated = authenticate_collector_token(
        token,
        expected_digest if isinstance(expected_digest, str) else None,
    )
    environ = request.environ
    tls_verified = bool(
        request.is_secure
        or environ.get("ananta.tls_verified") is True
        or environ.get("SSL_CLIENT_VERIFY") == "SUCCESS"
    )
    certificate = environ.get("SSL_CLIENT_CERT")
    return SfuNodeObservationAuthentication(
        transport_tls_verified=tls_verified,
        collector_authenticated=collector_authenticated,
        peer_certificate_pem=(certificate if isinstance(certificate, str) else None),
    )


def _service() -> SfuNodeObservationIngestionService:
    service = current_app.extensions.get("sfu_node_observation_ingestion_service")
    if not isinstance(service, SfuNodeObservationIngestionService):
        raise SfuNodeObservationError(
            "sfu_node_observation_service_unavailable",
            status_code=503,
        )
    return service


__all__ = ["webrtc_sfu_node_observations_bp"]
