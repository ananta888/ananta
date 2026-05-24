from __future__ import annotations

import logging
import time
from typing import Any

from flask import Blueprint, Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import BadRequest

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.audit_logger import AuditLogger
from agent.services.llm_interceptor.context_gate import ContextGate
from agent.services.llm_interceptor.model_profiles import load_model_profiles
from agent.services.llm_interceptor.policy_engine import PolicyEngine
from agent.services.llm_interceptor.prompt_adapter import PromptAdapter
from agent.services.llm_interceptor.repair_controller import RepairController
from agent.services.llm_interceptor.response_validator import ResponseValidator
from agent.services.llm_interceptor.provider_router import ProviderRouter
from agent.services.llm_interceptor.request_envelope import build_request_envelope
from agent.services.llm_interceptor.secret_redactor import SecretRedactor

logger = logging.getLogger(__name__)


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
        self._router = ProviderRouter(cfg)
        self._policy = PolicyEngine(cfg.policy.model_dump())
        self._redactor = SecretRedactor(cfg.redaction.model_dump())
        self._context_gate = ContextGate(cfg.policy.model_dump())
        self._model_profiles = load_model_profiles({"profiles": {"intercepted-coder": {"policy_preamble": "Follow system and security policy. Do not expose secrets.", "markdown_prone": True, "task_overrides": {"coding": {"markdown_prone": True}, "security": {"markdown_prone": False}}}}})
        self._prompt_adapter = PromptAdapter(self._model_profiles)
        self._validator = ResponseValidator()
        self._repair = RepairController(max_attempts=cfg.response_validation.structured_json_repair_attempts, enabled=True)
        self._audit = AuditLogger(debug_prompt_logging=False)

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
            started = time.time()
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
            envelope = build_request_envelope(payload=payload, headers=dict(request.headers))
            upstream, routed_model = self._router.resolve_route(payload=payload, envelope=envelope.as_dict())
            decision = self._policy.evaluate(envelope=envelope.as_dict(), upstream_trust_level=upstream.trust_level)
            if decision.action in {"deny", "local_only"} and upstream.trust_level == "cloud":
                return _error_response("policy_denied", code="policy_denied", status=403)

            redacted_messages, _meta = self._redactor.redact_messages(messages)
            context_snippets = list(payload.get("context_snippets") or [])
            gated_context, gate_meta = self._context_gate.gate(
                snippets=context_snippets,
                upstream_trust_level=upstream.trust_level,
                decision=decision.as_dict(),
                worker=envelope.caller_type,
            )
            adapted_messages = self._prompt_adapter.adapt_messages(
                messages=redacted_messages,
                model=routed_model,
                task_kind=str((envelope.task_metadata or {}).get("task_kind") or ""),
                require_strict_json=bool(payload.get("response_format") == "json_schema"),
            )
            forwarded = dict(payload)
            forwarded["messages"] = adapted_messages
            forwarded["model"] = routed_model
            if gated_context:
                forwarded["context_snippets"] = gated_context
            else:
                forwarded.pop("context_snippets", None)

            if bool(payload.get("stream", False)):
                try:
                    raw_iter = self._router.forward_chat_stream(payload=forwarded, envelope=envelope.as_dict())
                    def _validated():
                        for chunk in raw_iter:
                            ok, reason = self._validator.validate_stream_chunk(chunk.strip())
                            if not ok:
                                yield "data: {\"error\":\"invalid_stream_chunk\"}\n\n"
                                yield "data: [DONE]\n\n"
                                break
                            yield chunk
                    stream_iter = _validated()
                except ValueError as exc:
                    return _error_response(str(exc), code="upstream_error", status=502)
                logger.info(
                    "llm_interceptor_stream request_id=%s upstream=%s model=%s gate_denied=%s",
                    envelope.request_id,
                    upstream.id,
                    routed_model,
                    gate_meta.get("denied_count"),
                )
                return Response(stream_with_context(stream_iter), mimetype="text/event-stream")
            try:
                result = self._router.forward_chat(payload=forwarded, envelope=envelope.as_dict())
                valid, reason = self._validator.validate_chat_completion(result)
                if not valid:
                    repaired, repair_reason = self._repair.repair_chat_completion(result, model=routed_model)
                    if repaired is None:
                        return _error_response(f"response_validation_failed:{reason}", code="response_validation_failed", status=502)
                    result = repaired
                    logger.info("llm_interceptor_repair request_id=%s reason=%s", envelope.request_id, repair_reason)
                event = self._audit.build_event(
                    request_id=envelope.request_id,
                    caller_type=envelope.caller_type,
                    upstream_id=upstream.id,
                    model=routed_model,
                    policy_decision=decision.as_dict(),
                    redaction_meta=_meta,
                    duration_ms=int((time.time() - started) * 1000),
                    messages=None,
                )
                logger.info("llm_interceptor_audit %s", event)
                return result
            except ValueError as exc:
                return _error_response(str(exc), code="upstream_error", status=502)

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
