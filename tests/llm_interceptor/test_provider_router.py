from __future__ import annotations

import pytest

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.provider_router import ProviderRouter


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _cfg() -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "upstreams": [
                {
                    "id": "local",
                    "type": "openai_compatible",
                    "base_url": "http://local/v1",
                    "trust_level": "local",
                    "allowed_models": ["m-local"],
                }
            ],
            "routing": {"default_upstream": "local", "default_model": "m-local", "rules": []},
        }
    )


def test_forward_chat_posts_body(monkeypatch):
    captured = {}

    def _fake_post(url, json=None, headers=None, timeout=None, **_kwargs):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200, {"id": "x", "object": "chat.completion", "choices": []})

    monkeypatch.setattr("agent.services.llm_interceptor.provider_router.requests.post", _fake_post)
    out = ProviderRouter(_cfg()).forward_chat(payload={"model": "m-local", "messages": [{"role": "user", "content": "hi"}]})
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["model"] == "m-local"
    assert out["object"] == "chat.completion"


def test_disallowed_model_rejected():
    with pytest.raises(ValueError):
        ProviderRouter(_cfg()).forward_chat(payload={"model": "m-cloud", "messages": [{"role": "user", "content": "hi"}]})


def test_upstream_error_mapped(monkeypatch):
    monkeypatch.setattr(
        "agent.services.llm_interceptor.provider_router.requests.post",
        lambda *args, **kwargs: _Resp(500, {"error": "x"}),
    )
    with pytest.raises(ValueError):
        ProviderRouter(_cfg()).forward_chat(payload={"model": "m-local", "messages": [{"role": "user", "content": "hi"}]})

