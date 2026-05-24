from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Flask, jsonify, request
from werkzeug.exceptions import BadRequest

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig


def _error_response(message: str, *, code: str, status: int = 400):
    payload = {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "code": code,
        }
    }
    return jsonify(payload), status


class OpenAICompatInterceptorServer:
    """OpenAI-compatible interceptor HTTP surface (MVP skeleton)."""

    def __init__(self, cfg: LlmInterceptorConfig) -> None:
        self.cfg = cfg

    def _models_payload(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for up in self.cfg.upstreams:
            models = up.allowed_models or [self.cfg.routing.default_model]
            for model in models:
                items.append(
                    {
                        "id": model,
                        "object": "model",
                        "owned_by": f"interceptor:{up.id}",
                    }
                )
        return {"object": "list", "data": items}

    def _build_blueprint(self) -> Blueprint:
        bp = Blueprint("llm_interceptor_openai_compat", __name__)

        @bp.route("/health", methods=["GET"])
        def health():
            return {
                "status": "ok",
                "service": "llm_interceptor_openai_compat",
                "prefix": self.cfg.listen.prefix,
                "active_upstreams": [u.id for u in self.cfg.upstreams],
            }

        @bp.route("/models", methods=["GET"])
        def list_models():
            return self._models_payload()

        @bp.route("/chat/completions", methods=["POST"])
        def chat_completions():
            try:
                payload = request.get_json(force=False, silent=False)
            except BadRequest:
                return _error_response("invalid_json", code="invalid_json", status=400)
            if not isinstance(payload, dict):
                return _error_response("request_body_must_be_json_object", code="invalid_body", status=400)

            model = str(payload.get("model") or "").strip()
            messages = payload.get("messages")
            if not model:
                return _error_response("model_required", code="model_required", status=400)
            if not isinstance(messages, list) or not messages:
                return _error_response("messages_required", code="messages_required", status=400)

            # MVP skeleton: shape-compatible deterministic response.
            created = int(time.time())
            content = "interceptor_mvp_ack"
            return {
                "id": f"chatcmpl-interceptor-{created}",
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        return bp

    def create_app(self) -> Flask:
        app = Flask("ananta_llm_interceptor")
        app.register_blueprint(self._build_blueprint(), url_prefix=self.cfg.listen.prefix)
        return app

    def startup_summary(self) -> dict[str, Any]:
        """Safe startup metadata without secrets."""
        return {
            "listen": {
                "host": self.cfg.listen.host,
                "port": self.cfg.listen.port,
                "prefix": self.cfg.listen.prefix,
            },
            "upstreams": [
                {
                    "id": u.id,
                    "type": u.type,
                    "trust_level": u.trust_level,
                    "base_url": u.base_url,
                }
                for u in self.cfg.upstreams
            ],
        }


def create_interceptor_app(cfg: LlmInterceptorConfig) -> Flask:
    return OpenAICompatInterceptorServer(cfg).create_app()

