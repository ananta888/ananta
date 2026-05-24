from __future__ import annotations

import json

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.openai_compat_server import OpenAICompatInterceptorServer


def _cfg() -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "listen": {"host": "127.0.0.1", "port": 8787, "prefix": "/v1"},
            "upstreams": [
                {
                    "id": "local-lmstudio",
                    "type": "openai_compatible",
                    "base_url": "http://127.0.0.1:1234/v1",
                    "api_key_env": None,
                    "trust_level": "local",
                    "allowed_models": ["intercepted-coder"],
                }
            ],
            "routing": {"default_upstream": "local-lmstudio", "default_model": "intercepted-coder", "rules": []},
        }
    )


def test_health_exposes_active_upstream_ids_without_secrets():
    app = OpenAICompatInterceptorServer(_cfg()).create_app()
    client = app.test_client()
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["active_upstreams"] == ["local-lmstudio"]
    assert "api_key" not in json.dumps(body).lower()


def test_chat_completions_rejects_invalid_json():
    app = OpenAICompatInterceptorServer(_cfg()).create_app()
    client = app.test_client()
    resp = client.post("/v1/chat/completions", data="{bad", content_type="application/json")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "invalid_json"


def test_chat_completions_rejects_missing_messages_or_model():
    app = OpenAICompatInterceptorServer(_cfg()).create_app()
    client = app.test_client()
    resp = client.post("/v1/chat/completions", json={"messages": []})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "model_required"

    resp2 = client.post("/v1/chat/completions", json={"model": "intercepted-coder"})
    assert resp2.status_code == 400
    assert resp2.get_json()["error"]["code"] == "messages_required"


def test_chat_completions_accepts_valid_body_and_returns_openai_shape():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    server._router.forward_chat = lambda **_kwargs: {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "intercepted-coder",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "intercepted-coder",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["object"] == "chat.completion"
    assert isinstance(body.get("choices"), list)
    assert body["choices"][0]["message"]["role"] == "assistant"


def test_streaming_passthrough_returns_sse():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    server._router.forward_chat_stream = lambda **_kwargs: iter(["data: {\"id\":\"1\"}\n\n", "data: [DONE]\n\n"])
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "intercepted-coder", "messages": [{"role": "user", "content": "hello"}], "stream": True},
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    body = resp.get_data(as_text=True)
    assert "data: [DONE]" in body
