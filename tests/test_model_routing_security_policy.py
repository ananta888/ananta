"""Tests for Security Policy enforcement in model routing — AMR-016."""
import pytest
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    RoutingContext,
    SecurityPolicyChecker,
)


def _cloud(profile_id: str = "cloud-p") -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="openai",
        model="gpt-4o",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )


def _local(profile_id: str = "local-p") -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="ollama",
        model="qwen:7b",
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
    )


# ── secret pattern detection ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "api_key=sk-supersecret1234567890abcdef",
    "SECRET_KEY = abc1234567890",
    "password: mysecretpassword",
    "bearer eyJhbGciOiJIUzI1NiJ9.xxxxxxxxxxxxxxxxxxxxxx",
    "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "sk-abcdefghijklmnopqrstuvwx1234",
])
def test_context_has_secrets_true(text: str):
    checker = SecurityPolicyChecker()
    assert checker.context_has_secrets(text), f"Should detect secret in: {text!r}"


@pytest.mark.parametrize("text", [
    "",
    "this is a normal coding task",
    "implement a REST API endpoint for /users",
    "add error handling to the file reader",
])
def test_context_has_secrets_false(text: str):
    checker = SecurityPolicyChecker()
    assert not checker.context_has_secrets(text), f"Should NOT detect secret in: {text!r}"


# ── cloud blocked when secrets present ───────────────────────────────────────

def test_cloud_profile_blocked_when_secrets_in_context():
    resolver = ModelProfileResolver(
        profiles=[_cloud()],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret12345678901234")
    result = resolver.resolve(ctx)
    assert not result.ok
    assert any("security_policy" in reason for _, reason in result.blocked_candidates)


def test_local_profile_passes_even_when_secrets_present():
    resolver = ModelProfileResolver(
        profiles=[_local()],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret12345678901234")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "local-p"


def test_policy_can_disable_secret_blocking():
    resolver = ModelProfileResolver(
        profiles=[_cloud()],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=False),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret12345678901234")
    result = resolver.resolve(ctx)
    assert result.ok


# ── cloud not allowed without explicit opt-in ─────────────────────────────────

def test_cloud_without_cloud_allowed_flag_is_blocked():
    p = ModelProfile(
        profile_id="cloud-no-opt-in",
        provider_id="openai",
        model="gpt-4",
        cloud=True,
        cloud_allowed=False,
        block_secret_context=True,
    )
    resolver = ModelProfileResolver(profiles=[p])
    result = resolver.resolve(RoutingContext())
    assert not result.ok
    assert any("cloud_allowed=false" in r for _, r in result.blocked_candidates)


def test_cloud_missing_block_secret_context_blocked():
    p = ModelProfile(
        profile_id="cloud-unsafe",
        provider_id="openai",
        model="gpt-4",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=False,
    )
    resolver = ModelProfileResolver(profiles=[p])
    result = resolver.resolve(RoutingContext())
    assert not result.ok
    assert any("block_secret_context" in r for _, r in result.blocked_candidates)


# ── fallback to local when cloud is blocked ───────────────────────────────────

def test_falls_back_to_local_when_cloud_blocked():
    resolver = ModelProfileResolver(
        profiles=[_cloud("cloud-first"), _local("local-fallback")],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret12345678901234")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "local-fallback"


# ── allowed_cloud_providers allowlist ─────────────────────────────────────────

def test_allowed_cloud_providers_blocks_unknown_provider():
    p = _cloud()  # provider_id="openai"
    resolver = ModelProfileResolver(
        profiles=[p],
        security_policy=SecurityPolicyChecker(allowed_cloud_providers=["openrouter"]),
    )
    result = resolver.resolve(RoutingContext())
    assert not result.ok
    assert any("not_in_allowlist" in r for _, r in result.blocked_candidates)


def test_allowed_cloud_providers_permits_listed_provider():
    p = ModelProfile(
        profile_id="openrouter-p",
        provider_id="openrouter",
        model="llama-3.1",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
    )
    resolver = ModelProfileResolver(
        profiles=[p],
        security_policy=SecurityPolicyChecker(allowed_cloud_providers=["openrouter"]),
    )
    result = resolver.resolve(RoutingContext())
    assert result.ok
