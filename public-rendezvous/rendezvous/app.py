"""Ananta Public Rendezvous Service – standalone Flask-App.

Endpunkte:
  GET  /health
  GET  /info
  POST /rendezvous/sessions
  GET  /rendezvous/sessions
  POST /rendezvous/sessions/catalog
  POST /rendezvous/sessions/join
  POST /rendezvous/sessions/<id>/join
  GET  /rendezvous/sessions/<id>/participants
  PUT  /rendezvous/sessions/<id>/membership/runtime
  GET  /rendezvous/sessions/<id>/security/key-packages
  GET/POST /rendezvous/sessions/<id>/security/key-confirmations
  PATCH /rendezvous/sessions/<id>/permissions
  DELETE /rendezvous/sessions/<id>/membership
  DELETE /rendezvous/sessions/<id>
  GET  /rendezvous/turn-credentials?session_id=<id>
  POST /webrtc/sessions/<id>/signal
  GET  /webrtc/sessions/<id>/signal
  GET/POST /signaling          (HTTP-Polling-Alias für WebSocket-kompatible Clients)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import service as svc
from flask import Flask, jsonify, request
from oidc_auth import AuthContext, verify_bearer_token  # noqa: F401 - AuthContext re-exported
from pair_security import SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS
from rendezvous_request_support import RequestSupport, TokenVerifier
from rendezvous_session_routes import SessionRoutes
from rendezvous_transport_routes import TURN_CREDENTIAL_ERROR_STATUS, TransportRoutes

import config as cfg

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

_TURN_CREDENTIAL_ERROR_STATUS = TURN_CREDENTIAL_ERROR_STATUS


def _register_cors(app: Flask, config: Any) -> None:
    @app.after_request
    def add_cors_headers(response):
        """Allow only explicitly configured browser app origins."""
        origin = str(request.headers.get("Origin") or "").rstrip("/")
        if origin and origin in config.CORS_ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Ananta-Peer-Id, X-Ananta-Device-Id, X-Ananta-Membership-Capability"
            )
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "Retry-After"
            response.headers["Access-Control-Max-Age"] = "600"
        return response


def _register_health_and_info(app: Flask, config: Any) -> None:
    @app.get("/health")
    def health():
        return jsonify({"ok": True, "service": "ananta-rendezvous"}), 200

    @app.get("/info")
    def info():
        return jsonify(
            {
                "service": "ananta-rendezvous",
                "oidc_issuer": config.OIDC_ISSUER,
                "turn_realm": config.TURN_REALM,
                "turn_urls": config.TURN_URLS,
                "session_max_minutes": config.SESSION_MAX_DURATION_SECONDS // 60,
                "supported_identity_binding_versions": [1, 2],
                "supported_public_media_e2ee_versions": list(SUPPORTED_PUBLIC_MEDIA_E2EE_VERSIONS),
            }
        ), 200


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_):
        return jsonify({"error": "method_not_allowed"}), 405

    @app.errorhandler(413)
    def request_too_large(_):
        return jsonify({"error": "request_too_large"}), 413

    @app.errorhandler(500)
    def internal_error(exc):
        log.exception("Internal error: %s", exc)
        return jsonify({"error": "internal_error"}), 500


def create_app(
    *,
    service: Any = svc,
    verify_token: TokenVerifier = verify_bearer_token,
    config: Any = cfg,
) -> Flask:
    """Build the rendezvous Flask app around an injected service, token verifier and config.

    The defaults are the production collaborators (the ``service`` module
    facade, OIDC bearer verification and ``config``); tests pass doubles
    instead of patching module attributes.
    """
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    _register_cors(app, config)
    _register_health_and_info(app, config)
    support = RequestSupport(verify_token=verify_token, service=service, config=config, logger=log)
    session_routes = SessionRoutes(support=support, service=service, config=config, logger=log)
    session_routes.register(app)
    transport_routes = TransportRoutes(support=support, service=service, config=config)
    transport_routes.register(app)
    _register_error_handlers(app)
    app.extensions["ananta_rendezvous"] = {
        "request_support": support,
        "session_routes": session_routes,
        "transport_routes": transport_routes,
    }
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
