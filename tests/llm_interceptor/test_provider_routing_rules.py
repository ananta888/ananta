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
                    "allowed_models": ["m-local"],
                },
                {
                    "id": "cloud",
                    "type": "openrouter_compatible",
                    "base_url": "https://cloud/v1",
                    "trust_level": "cloud",
                    "allowed_models": ["m-cloud"],
                },
            ],
            "routing": {
                "default_upstream": "local",
                "default_model": "m-local",
                "rules": [
                    {
                        "when": {"worker": "opencode", "task_kind": "coding", "risk_lte": "medium"},
                        "upstream": "local",
                        "model": "m-local",
                    },
                    {
                        "when": {"requires_cloud": True, "context_class": "public_or_redacted"},
                        "upstream": "cloud",
                        "model": "m-cloud",
                    },
                ],
            },
        }
    )


def test_worker_cannot_force_disallowed_cloud_model():
    router = ProviderRouter(_cfg())
    upstream, model = router.resolve_route(
        payload={
            "model": "evil-cloud",
            "caller": {"source": "opencode"},
            "task": {"task_kind": "coding", "risk": "low"},
            "messages": [{"role": "user", "content": "x"}],
        },
        envelope={},
    )
    assert upstream.id == "local"
    assert model == "m-local"


def test_cloud_rule_selected_only_with_cloud_metadata():
    router = ProviderRouter(_cfg())
    upstream, model = router.resolve_route(
        payload={
            "model": "m-local",
            "task": {"requires_cloud": True, "context_class": "public_or_redacted"},
            "messages": [{"role": "user", "content": "x"}],
        },
        envelope={},
    )
    assert upstream.id == "cloud"
    assert model == "m-cloud"

