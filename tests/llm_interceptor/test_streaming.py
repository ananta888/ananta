from __future__ import annotations

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.provider_router import ProviderRouter


class _StreamResp:
    def __init__(self, status_code=200, lines=None):
        self.status_code = status_code
        self._lines = lines or []

    def iter_lines(self, decode_unicode=True):
        _ = decode_unicode
        for line in self._lines:
            yield line


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


def test_forward_chat_stream_preserves_order(monkeypatch):
    monkeypatch.setattr(
        "agent.services.llm_interceptor.provider_router.requests.post",
        lambda *args, **kwargs: _StreamResp(lines=["data: {\"a\":1}", "data: [DONE]"]),
    )
    out = list(ProviderRouter(_cfg()).forward_chat_stream(payload={"model": "m-local", "messages": [{"role": "user", "content": "h"}], "stream": True}))
    assert out[0].startswith("data: ")
    assert "DONE" in out[-1]

