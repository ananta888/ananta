from __future__ import annotations

from agent.services.llm_interceptor.config_schema import LlmInterceptorConfig
from agent.services.llm_interceptor.provider_router import ProviderRouter


def _cfg() -> LlmInterceptorConfig:
    return LlmInterceptorConfig.model_validate(
        {
            "upstreams": [
                {
                    "id": "local",
                    "type": "openai_compatible",
                    "base_url": "http://local/v1",
                    "trust_level": "local",
                    "allowed_models": ["intercepted-coder"],
                }
            ],
            "routing": {
                "default_upstream": "local",
                "default_model": "intercepted-coder",
                "model_aliases": {
                    "ananta-interceptor/intercepted-coder": "intercepted-coder",
                    "intercepted-coder": "intercepted-coder",
                },
                "rules": [],
            },
        }
    )


def test_opencode_alias_maps_to_internal_model():
    router = ProviderRouter(_cfg())
    upstream, model = router.resolve_route(
        payload={"model": "ananta-interceptor/intercepted-coder", "messages": [{"role": "user", "content": "hi"}]},
        envelope={"caller_metadata": {"source": "opencode"}},
    )
    assert upstream.id == "local"
    assert model == "intercepted-coder"

