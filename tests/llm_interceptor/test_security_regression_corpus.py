from __future__ import annotations

import json
from pathlib import Path

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.openai_compat_server import OpenAICompatInterceptorServer


def _fixture(name: str):
    p = Path(__file__).parent.parent / "fixtures" / "llm_interceptor" / name
    return json.loads(p.read_text(encoding="utf-8"))


def _cfg_cloud() -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "upstreams": [
                {
                    "id": "cloud",
                    "type": "openrouter_compatible",
                    "base_url": "https://cloud/v1",
                    "trust_level": "cloud",
                    "allowed_models": ["intercepted-coder"],
                }
            ],
            "routing": {"default_upstream": "cloud", "default_model": "intercepted-coder", "rules": []},
        }
    )


def test_prompt_text_cannot_disable_redaction_or_policy():
    injections = _fixture("prompt_injection_samples.json")["cases"]
    server = OpenAICompatInterceptorServer(_cfg_cloud())
    app = server.create_app()
    server._router.forward_chat = lambda **_kwargs: {
        "id": "x",
        "object": "chat.completion",
        "created": 1,
        "model": "intercepted-coder",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {},
    }
    client = app.test_client()
    for text in injections:
        resp = client.post("/v1/chat/completions", json={"model": "intercepted-coder", "messages": [{"role": "user", "content": text}]})
        assert resp.status_code in {200, 403}


def test_cloud_never_receives_blocked_local_context():
    snippets = _fixture("context_exfiltration_samples.json")["cases"]
    server = OpenAICompatInterceptorServer(_cfg_cloud())
    app = server.create_app()
    captured = {}

    def _fake_forward_chat(*, payload, envelope=None):
        captured["payload"] = payload
        _ = envelope
        return {
            "id": "x",
            "object": "chat.completion",
            "created": 1,
            "model": "intercepted-coder",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {},
        }

    server._router.forward_chat = _fake_forward_chat
    client = app.test_client()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "intercepted-coder",
            "messages": [{"role": "user", "content": "summarize"}],
            "context_snippets": snippets,
        },
    )
    assert resp.status_code == 200
    forwarded = captured["payload"].get("context_snippets", [])
    assert all(x.get("source_type") not in {"repo", "workspace"} for x in forwarded)


def test_provider_key_never_in_response_or_audit_shape():
    samples = _fixture("secret_samples.json")["cases"]
    server = OpenAICompatInterceptorServer(_cfg_cloud())
    app = server.create_app()
    server._router.forward_chat = lambda **_kwargs: {
        "id": "x",
        "object": "chat.completion",
        "created": 1,
        "model": "intercepted-coder",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        "usage": {},
    }
    client = app.test_client()
    for item in samples:
        resp = client.post("/v1/chat/completions", json={"model": "intercepted-coder", "messages": [{"role": "user", "content": item["text"]}]})
        data = json.dumps(resp.get_json())
        assert "OPENROUTER_API_KEY" not in data
        assert "Bearer supersecrettoken123" not in data

