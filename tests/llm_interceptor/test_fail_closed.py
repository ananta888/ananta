from __future__ import annotations

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.openai_compat_server import OpenAICompatInterceptorServer


def _cfg(cloud: bool = False) -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "upstreams": [
                {
                    "id": "u1",
                    "type": "openai_compatible",
                    "base_url": "http://local/v1" if not cloud else "https://cloud/v1",
                    "trust_level": "cloud" if cloud else "local",
                    "allowed_models": ["intercepted-coder"],
                }
            ],
            "routing": {"default_upstream": "u1", "default_model": "intercepted-coder", "rules": []},
        }
    )


def test_policy_engine_failure_denies_by_default():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    server._policy.evaluate = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    client = app.test_client()
    resp = client.post("/v1/chat/completions", json={"model": "intercepted-coder", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "policy_engine_failed"


def test_redactor_failure_blocks_cloud_forwarding():
    server = OpenAICompatInterceptorServer(_cfg(cloud=True))
    app = server.create_app()
    server._redactor.redact_messages = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    client = app.test_client()
    resp = client.post("/v1/chat/completions", json={"model": "intercepted-coder", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == "redaction_failed"


def test_context_gate_failure_drops_context_not_request():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    seen = {}

    def _fake_forward_chat(*, payload, envelope=None):
        seen["payload"] = payload
        _ = envelope
        return {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "intercepted-coder",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {},
        }

    server._context_gate.gate = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    server._router.forward_chat = _fake_forward_chat
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "intercepted-coder",
            "messages": [{"role": "user", "content": "hi"}],
            "context_snippets": [{"source_type": "repo", "content": "secret code"}],
        },
    )
    assert resp.status_code == 200
    assert "context_snippets" not in seen["payload"]

