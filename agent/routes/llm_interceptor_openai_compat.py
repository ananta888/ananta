"""LLM interceptor HTTP surface (CLN-053): thin routes over the proxy service.

Blueprints are defined in routes only; the interception domain logic lives in
``agent.services.llm_interceptor.openai_compat_server``. This module owns the
composition root: app assembly and the dev-server entry point.
"""

from __future__ import annotations

import logging

from flask import Blueprint, Flask, request
from werkzeug.exceptions import BadRequest

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.openai_compat_server import (
    OpenAICompatInterceptorServer,
    _error_response,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_openai_compat_blueprint",
    "create_interceptor_app",
    "run_interceptor_server",
]


def build_openai_compat_blueprint(server: OpenAICompatInterceptorServer) -> Blueprint:
    """Expose the interceptor service as three thin HTTP handlers."""
    bp = Blueprint("llm_interceptor_openai_compat", __name__)

    @bp.route("/health", methods=["GET"])
    def health():
        return server.health_payload()

    @bp.route("/models", methods=["GET"])
    def list_models():
        return server._models_payload()

    @bp.route("/chat/completions", methods=["POST"])
    def chat_completions():
        try:
            payload = request.get_json(force=False, silent=False)
        except BadRequest:
            return _error_response("invalid_json", code="invalid_json", status=400)
        return server.chat_completion(payload, dict(request.headers))

    return bp


def create_interceptor_app(server: OpenAICompatInterceptorServer) -> Flask:
    app = Flask("ananta_llm_interceptor")
    app.register_blueprint(build_openai_compat_blueprint(server), url_prefix=server.cfg.listen.prefix)
    return app


def run_interceptor_server(cfg: LlmInterceptorConfig) -> None:
    server = OpenAICompatInterceptorServer(cfg)
    summary = server.startup_summary()
    logger.info(
        "llm_interceptor_start host=%s port=%s prefix=%s upstream_ids=%s",
        summary["listen"]["host"],
        summary["listen"]["port"],
        summary["listen"]["prefix"],
        [u["id"] for u in summary["upstreams"]],
    )
    app = create_interceptor_app(server)
    app.run(host=cfg.listen.host, port=cfg.listen.port)
