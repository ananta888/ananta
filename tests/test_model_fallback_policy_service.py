from agent.services.model_fallback_policy_service import ModelFallbackPolicyService
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ProviderHealthCache


def _profile(profile_id: str, provider_id: str = "lmstudio") -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id=provider_id,
        model="auto",
        local=provider_id == "lmstudio",
        cloud=provider_id != "lmstudio",
        cloud_allowed=provider_id != "lmstudio",
        block_secret_context=provider_id != "lmstudio",
    )


def test_fallback_policy_covers_expected_error_classes():
    service = ModelFallbackPolicyService()
    previous = _profile("local")
    next_profile = _profile("gemma", provider_id="openrouter")

    for error_type in [
        "provider_unavailable",
        "timeout",
        "http_5xx",
        "invalid_json_response",
        "empty_content",
        "schema_validation_failed",
        "tool_not_allowed",
        "tool_args_invalid",
        "repeated_tool_failure",
        "context_too_large",
    ]:
        decision = service.should_fallback(
            error_type=error_type,
            previous_profile=previous,
            next_profile=next_profile,
        )
        assert not decision.terminal, error_type
        assert decision.next_profile_id == "gemma"


def test_policy_blocked_is_terminal():
    decision = ModelFallbackPolicyService().should_fallback(
        error_type="policy_blocked",
        previous_profile=_profile("gemma", provider_id="openrouter"),
        next_profile=_profile("qwen", provider_id="openrouter"),
    )
    assert decision.terminal
    assert decision.next_profile_id is None


def test_provider_health_marked_on_timeout():
    cache = ProviderHealthCache()
    profile = _profile("local")
    assert cache.is_available("lmstudio")
    ModelFallbackPolicyService(cache).should_fallback(
        error_type="timeout",
        previous_profile=profile,
        next_profile=_profile("gemma", provider_id="openrouter"),
    )
    assert not cache.is_available("lmstudio")
