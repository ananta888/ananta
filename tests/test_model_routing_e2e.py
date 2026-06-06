"""E2E tests for model routing system — AMR-021."""
import json
import os
import tempfile
import pytest

from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    ProviderHealthCache,
    RoutingContext,
    RoutingRules,
    SecurityPolicyChecker,
)
from agent.services.blueprint_model_policy_service import (
    extract_blueprint_model_policy,
    build_routing_context_kwargs_from_blueprint_policy,
)
from agent.services.template_model_policy_service import TemplateModelPolicyService


# ── helpers ─────────────────────────────────────────────────────────────────

def _local(pid: str, role: str = "any", **kw) -> ModelProfile:
    return ModelProfile(
        profile_id=pid, provider_id="ollama", model="qwen:7b",
        model_role=role, local=True, cloud=False,
        cloud_allowed=False, block_secret_context=False, **kw
    )


def _cloud(pid: str, **kw) -> ModelProfile:
    return ModelProfile(
        profile_id=pid, provider_id="openai", model="gpt-4o",
        model_role="planner", local=False, cloud=True,
        cloud_allowed=True, block_secret_context=True, **kw
    )


# ── AMR-011: TaskRoutingContract fields ─────────────────────────────────────

def test_task_routing_contract_has_model_profile_fields():
    from agent.models import TaskRoutingContract
    c = TaskRoutingContract(
        model_profile_id="local-coder",
        model_role="coder",
        model_resolver_source="blueprint_rule",
        model_resolver_rank=2,
        model_policy_decisions=["accepted"],
        model_blocked_candidates=[],
        model_cloud_allowed=False,
        model_block_secret_context=False,
    )
    assert c.model_profile_id == "local-coder"
    assert c.model_resolver_rank == 2
    assert c.model_cloud_allowed is False


# ── AMR-012: Blueprint model policy ─────────────────────────────────────────

def test_extract_blueprint_model_policy_with_preferred_profile():
    policy = extract_blueprint_model_policy(
        "role-123",
        {"model_policy": {"preferred_profile_id": "local-coder", "model_role": "coder"}},
    )
    assert policy is not None
    assert policy.preferred_profile_id == "local-coder"
    assert policy.model_role == "coder"


def test_extract_blueprint_model_policy_returns_none_without_policy():
    policy = extract_blueprint_model_policy("role-123", {"some_other_key": "value"})
    assert policy is None


def test_extract_blueprint_model_policy_filters_unknown_capabilities():
    policy = extract_blueprint_model_policy(
        "role-123",
        {"model_policy": {"required_capabilities": ["supports_json", "supports_flying"]}},
    )
    assert policy is not None
    assert "supports_json" in policy.required_capabilities
    assert "supports_flying" not in policy.required_capabilities


def test_blueprint_policy_builds_routing_context_kwargs():
    policy = extract_blueprint_model_policy(
        "role-123",
        {"model_policy": {"model_role": "coder", "required_capabilities": ["supports_tools", "supports_json"]}},
    )
    kwargs = build_routing_context_kwargs_from_blueprint_policy(policy)
    assert kwargs["model_role"] == "coder"
    assert kwargs["requires_tools"] is True
    assert kwargs["requires_json"] is True


# ── AMR-013: Template model policy ──────────────────────────────────────────

def test_template_policy_from_legacy_overrides():
    svc = TemplateModelPolicyService(agent_config={
        "template_model_overrides": {"my-template": "local-coder"}
    })
    policy = svc.resolve("my-template")
    assert policy is not None
    assert policy.preferred_profile_id == "local-coder"
    assert policy.source == "template_model_overrides"


def test_template_policy_from_new_style_policies():
    svc = TemplateModelPolicyService(agent_config={
        "template_model_policies": {
            "my-template": {"preferred_profile_id": "cloud-reviewer", "model_role": "reviewer", "allow_cloud": True}
        }
    })
    policy = svc.resolve("my-template")
    assert policy is not None
    assert policy.preferred_profile_id == "cloud-reviewer"
    assert policy.allow_cloud is True


def test_template_policy_returns_none_for_unknown_template():
    svc = TemplateModelPolicyService(agent_config={})
    assert svc.resolve("unknown-template") is None


def test_template_policy_all_policies_aggregates():
    svc = TemplateModelPolicyService(agent_config={
        "template_model_overrides": {"tmpl-a": "p1"},
        "template_model_policies": {"tmpl-b": {"preferred_profile_id": "p2"}},
    })
    all_p = svc.all_policies()
    ids = [p.template_id for p in all_p]
    assert "tmpl-a" in ids
    assert "tmpl-b" in ids


# ── AMR-017: Provider health + fallback ─────────────────────────────────────

def test_provider_health_cache_marks_unavailable():
    cache = ProviderHealthCache()
    cache.mark_unavailable("ollama")
    assert not cache.is_available("ollama")


def test_provider_health_cache_unknown_is_available():
    cache = ProviderHealthCache()
    assert cache.is_available("unknown-provider")


def test_provider_health_cache_reset_restores_availability():
    cache = ProviderHealthCache()
    cache.mark_unavailable("ollama")
    cache.reset("ollama")
    assert cache.is_available("ollama")


def test_resolver_skips_unhealthy_provider_falls_to_next():
    cache = ProviderHealthCache()
    cache.mark_unavailable("ollama")

    p1 = _local("p-ollama")           # provider ollama → skipped
    p2 = ModelProfile(
        profile_id="p-lmstudio", provider_id="lmstudio", model="m",
        local=True, cloud=False, cloud_allowed=False, block_secret_context=False,
    )
    resolver = ModelProfileResolver(profiles=[p1, p2], health_cache=cache)
    result = resolver.resolve(RoutingContext())
    assert result.ok
    assert result.profile.profile_id == "p-lmstudio"


def test_resolve_with_fallback_returns_legacy_info_when_no_profiles():
    resolver = ModelProfileResolver(profiles=[])
    result, fallback_info = resolver.resolve_with_fallback(
        RoutingContext(), legacy_provider="lmstudio", legacy_model="auto"
    )
    assert not result.ok
    assert fallback_info["legacy_provider"] == "lmstudio"
    assert "degraded_to_legacy" in fallback_info["reason"]


def test_resolve_with_fallback_returns_empty_fallback_info_when_ok():
    resolver = ModelProfileResolver(profiles=[_local("p1")])
    result, fallback_info = resolver.resolve_with_fallback(RoutingContext())
    assert result.ok
    assert fallback_info == {}


# ── AMR-020: ModelProfileLoader from file ───────────────────────────────────

def test_loader_reads_json_file():
    data = {"version": "1.0", "profiles": [
        {"profile_id": "test-p", "provider_id": "ollama", "model": "qwen:7b"}
    ]}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = f.name
    try:
        result = ModelProfileLoader().load_file(tmp_path)
        assert result.ok
        assert result.profiles[0].profile_id == "test-p"
    finally:
        os.unlink(tmp_path)


def test_override_normalization_from_env(monkeypatch):
    monkeypatch.setenv("DEFAULT_PROVIDER", "ollama")
    monkeypatch.setenv("DEFAULT_MODEL", "llama3:8b")
    from agent.services.model_override_normalization_service import ModelOverrideNormalizationService
    svc = ModelOverrideNormalizationService()
    result = svc.from_env()
    assert result is not None
    assert result.profile.provider_id == "ollama"
    assert result.profile.model == "llama3:8b"


def test_override_normalization_from_env_returns_none_when_not_set(monkeypatch):
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    from agent.services.model_override_normalization_service import ModelOverrideNormalizationService
    svc = ModelOverrideNormalizationService()
    assert svc.from_env() is None
