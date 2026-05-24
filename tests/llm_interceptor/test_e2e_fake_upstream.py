from __future__ import annotations

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.openai_compat_server import OpenAICompatInterceptorServer


def _cfg() -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "upstreams": [
                {
                    "id": "local",
                    "type": "openai_compatible",
                    "base_url": "http://fake/v1",
                    "trust_level": "local",
                    "allowed_models": ["intercepted-coder"],
                }
            ],
            "routing": {"default_upstream": "local", "default_model": "intercepted-coder", "rules": []},
        }
    )


def test_e2e_non_stream_redacts_before_upstream():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    captured = {}

    def _fake_forward_chat(*, payload, envelope=None):
        captured["payload"] = payload
        _ = envelope
        return {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": payload["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {},
        }

    server._router.forward_chat = _fake_forward_chat
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "intercepted-coder",
            "messages": [{"role": "user", "content": "token: abc123456"}],
        },
    )
    assert resp.status_code == 200
    assert "[REDACTED]" in captured["payload"]["messages"][-1]["content"]
    assert resp.get_json()["object"] == "chat.completion"


def test_e2e_streaming_variant():
    server = OpenAICompatInterceptorServer(_cfg())
    app = server.create_app()
    server._router.forward_chat_stream = lambda **_kwargs: iter(["data: {\"id\":\"x\"}\n\n", "data: [DONE]\n\n"])
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "intercepted-coder", "messages": [{"role": "user", "content": "hello"}], "stream": True},
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data: [DONE]" in body

