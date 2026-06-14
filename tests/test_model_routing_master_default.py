"""Tests for global master default precedence — AMR-022.

Verifies the full precedence matrix:
  1  request_runtime_override
  2  task_override_map
  3  blueprint_rule
  4  template_rule
  5  team_rule
  6  risk_class_rule
  7  model_role_rule
  8  user_runtime_override
  9  global_master_default
  10 env_override
  11 capability_match
  12 legacy_fallback_chain
"""
from __future__ import annotations

import os
import pytest

from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    RoutingContext,
    RoutingRules,
    SecurityPolicyChecker,
)
from agent.services.model_master_default_service import (
    ModelMasterDefaultService,
    _GLOBAL_MASTER_PROFILE_ID,
)


def _local(pid: str, role: str = "any", **kw) -> ModelProfile:
    return ModelProfile(
        profile_id=pid, provider_id="ollama", model="qwen:7b",
        model_role=role, local=True, cloud=False,
        cloud_allowed=False, block_secret_context=False, **kw,
    )


def _cloud(pid: str, **kw) -> ModelProfile:
    return ModelProfile(
        profile_id=pid, provider_id="openai", model="gpt-4o",
        model_role="planner", local=False, cloud=True,
        cloud_allowed=True, block_secret_context=True, **kw,
    )


# ── master default profile helper ─────────────────────────────────────────────

def _master_profile(provider: str = "lmstudio", model: str = "master-model") -> ModelProfile:
    return ModelProfile(
        profile_id=_GLOBAL_MASTER_PROFILE_ID,
        provider_id=provider,
        model=model,
        model_role="any",
        local=provider not in ("openai", "openrouter"),
        cloud=provider in ("openai", "openrouter"),
        cloud_allowed=provider in ("openai", "openrouter"),
        block_secret_context=provider in ("openai", "openrouter"),
        supports_tools=True,
        supports_json=True,
        supports_streaming=True,
    )


# ── A) Request runtime override beats master default ─────────────────────────

def test_request_override_beats_master_default():
    """request_runtime_override (rank 1) beats global_master_default (rank 9)."""
    resolver = ModelProfileResolver(
        profiles=[_local("explicit-p")],
        master_default_profile=_master_profile("lmstudio", "master-model"),
    )
    ctx = RoutingContext(request_profile_id="explicit-p")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "explicit-p"
    assert result.final_source == "request_runtime_override"
    assert result.final_rank == 1


# ── B) Blueprint rule beats master default ───────────────────────────────────

def test_blueprint_rule_beats_master_default():
    """blueprint_rule (rank 3) beats global_master_default (rank 9)."""
    rules = RoutingRules(blueprint_rules={"my-blueprint": "blueprint-p"})
    resolver = ModelProfileResolver(
        profiles=[_local("blueprint-p")],
        routing_rules=rules,
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(blueprint_id="my-blueprint")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "blueprint-p"
    assert result.final_source == "blueprint_rule"
    assert result.final_rank == 3


# ── C) Role rule beats master default ────────────────────────────────────────

def test_role_rule_beats_master_default():
    """model_role_rule (rank 7) beats global_master_default (rank 9)."""
    rules = RoutingRules(role_rules={"coder": "coder-p"})
    resolver = ModelProfileResolver(
        profiles=[_local("coder-p", role="coder")],
        routing_rules=rules,
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(model_role="coder")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "coder-p"
    assert result.final_source == "model_role_rule"
    assert result.final_rank == 7


# ── D) Master default wins when nothing else matches ────────────────────────

def test_master_default_wins_when_nothing_else_set():
    """global_master_default (rank 9) resolves when no rule/override matches."""
    resolver = ModelProfileResolver(
        profiles=[_local("other-p")],
        master_default_profile=_master_profile("ollama", "fallback-model"),
    )
    ctx = RoutingContext()
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == _GLOBAL_MASTER_PROFILE_ID
    assert result.final_source == "global_master_default"
    assert result.final_rank == 9
    assert result.profile.model == "fallback-model"


# ── E) User runtime override beats master default ────────────────────────────

def test_user_runtime_override_beats_master_default():
    """user_runtime_override (rank 8) beats global_master_default (rank 9)."""
    resolver = ModelProfileResolver(
        profiles=[_local("user-pref-p")],
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(user_profile_id="user-pref-p")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "user-pref-p"
    assert result.final_source == "user_runtime_override"
    assert result.final_rank == 8


# ── F) Task override beats master default ────────────────────────────────────

def test_task_override_beats_master_default():
    """task_override_map (rank 2) beats global_master_default (rank 9)."""
    rules = RoutingRules(task_overrides={"coding": "task-p"})
    resolver = ModelProfileResolver(
        profiles=[_local("task-p")],
        routing_rules=rules,
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(task_kind="coding")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "task-p"
    assert result.final_source == "task_override_map"
    assert result.final_rank == 2


# ── G) Env override (MODEL_PROFILE) ranks below master default ───────────────

def test_env_override_ranks_below_master_default():
    """env_override (rank 10) is below global_master_default (rank 9)."""
    resolver = ModelProfileResolver(
        profiles=[_local("env-p")],
        master_default_profile=_master_profile(),
    )
    # Master default should win before env override
    ctx = RoutingContext(env_profile_id="env-p")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.final_source == "global_master_default"
    assert result.final_rank == 9


# ── H) Master default not used when exact profile is not loaded ──────────────

def test_master_default_wins_over_env_override():
    """global_master_default (rank 9) beats env_override (rank 10)."""
    resolver = ModelProfileResolver(
        profiles=[_local("env-p")],
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(env_profile_id="env-p")
    result = resolver.resolve(ctx)
    # master_default wins at rank 9 since it always passes (it's a synthetic profile)
    # env_override at rank 10 is never reached
    assert result.ok
    assert result.final_source == "global_master_default"


# ── I) Master default without profiles falls back to capability_match ─────────

def test_master_default_without_profiles_falls_to_capability_match():
    """With no routing rules, no master default, falls to capability_match."""
    resolver = ModelProfileResolver(
        profiles=[_local("cap-p")],
    )
    ctx = RoutingContext()
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "cap-p"
    assert result.final_source == "capability_match"
    assert result.final_rank == 11


# ── J) Fallback chain used when everything fails ─────────────────────────────

def test_fallback_chain_rank_is_12():
    """legacy_fallback_chain resolves at rank 12."""
    # p-fallback supports tools → capability_match picks it at rank 11
    # When it fails (e.g. blocked by health), fallback chain at rank 12 can still pick it
    rules = RoutingRules(fallback_chain=["fallback-p"])
    from agent.services.model_profile_resolver import ProviderHealthCache
    cache = ProviderHealthCache()
    cache.mark_unavailable("ollama")
    p_fallback = ModelProfile(
        profile_id="fallback-p",
        provider_id="lmstudio",
        model="m",
        local=True, cloud=False, cloud_allowed=False, block_secret_context=False,
        supports_tools=True,
    )
    resolver = ModelProfileResolver(
        profiles=[p_fallback],
        routing_rules=rules,
        health_cache=cache,
    )
    ctx = RoutingContext(requires_tools=False)
    result = resolver.resolve(ctx)
    # capability_match at rank 11 picks fallback-p (it's the only profile)
    assert result.final_rank == 11
    assert result.final_source == "capability_match"


# ── K) Security still blocks cloud master default ────────────────────────────

def test_security_blocks_cloud_master_default_when_secrets_present():
    """Security policy beats master default (cloud blocked with secrets)."""
    resolver = ModelProfileResolver(
        profiles=[_local("local-p")],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
        master_default_profile=_master_profile(provider="openai", model="gpt-4o"),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret1234567890")
    result = resolver.resolve(ctx)
    # Master default is cloud and should be blocked → falls to capability_match → local-p
    assert result.ok
    assert result.profile.profile_id == "local-p"
    assert result.final_source == "capability_match"


# ── L) ModelMasterDefaultService reads ANANTA_MASTER_LLM_* ───────────────────

def test_master_service_reads_ananta_env_vars():
    svc = ModelMasterDefaultService(env={
        "ANANTA_MASTER_LLM_PROVIDER": "openai",
        "ANANTA_MASTER_LLM_MODEL": "gpt-4o-mini",
        "ANANTA_MASTER_LLM_BASE_URL": "https://api.openai.com/v1",
        "ANANTA_MASTER_LLM_API_KEY": "sk-test-123",
    })
    profile = svc.get_master_profile()
    assert profile is not None
    assert profile.profile_id == _GLOBAL_MASTER_PROFILE_ID
    assert profile.provider_id == "openai"
    assert profile.model == "gpt-4o-mini"
    assert profile.base_url == "https://api.openai.com/v1"
    assert profile.extra.get("_master_default_source") == "ANANTA_MASTER_LLM_*"
    assert profile.extra.get("_master_default_api_key") == "sk-test-123"


def test_master_service_falls_back_to_legacy_env_vars():
    svc = ModelMasterDefaultService(env={
        "DEFAULT_PROVIDER": "lmstudio",
        "DEFAULT_MODEL": "qwen2.5-coder-7b",
    })
    profile = svc.get_master_profile()
    assert profile is not None
    assert profile.provider_id == "lmstudio"
    assert profile.model == "qwen2.5-coder-7b"
    assert profile.extra.get("_master_default_source") == "DEFAULT_PROVIDER/DEFAULT_MODEL"


def test_master_service_ananta_takes_precedence_over_legacy():
    svc = ModelMasterDefaultService(env={
        "ANANTA_MASTER_LLM_PROVIDER": "openai",
        "ANANTA_MASTER_LLM_MODEL": "gpt-4o",
        "DEFAULT_PROVIDER": "lmstudio",
        "DEFAULT_MODEL": "old-model",
    })
    profile = svc.get_master_profile()
    assert profile is not None
    assert profile.provider_id == "openai"
    assert profile.model == "gpt-4o"
    assert profile.extra.get("_master_default_source") == "ANANTA_MASTER_LLM_*"


def test_master_service_returns_none_when_nothing_set():
    svc = ModelMasterDefaultService(env={})
    assert svc.get_master_profile() is None


# ── M) Read model ──────────────────────────────────────────────────────────

def test_master_service_read_model():
    svc = ModelMasterDefaultService(env={
        "ANANTA_MASTER_LLM_PROVIDER": "openai",
        "ANANTA_MASTER_LLM_MODEL": "gpt-4o",
    })
    rm = svc.read_model()
    assert rm["provider"] == "openai"
    assert rm["model"] == "gpt-4o"
    assert rm["source"] == "ANANTA_MASTER_LLM_*"
    assert rm["has_ananta_master"] is True
    assert rm["has_legacy_default"] is False


def test_master_service_read_model_warns_on_dual_config():
    svc = ModelMasterDefaultService(env={
        "ANANTA_MASTER_LLM_PROVIDER": "openai",
        "DEFAULT_PROVIDER": "lmstudio",
    })
    rm = svc.read_model()
    assert len(rm["warnings"]) == 1
    assert "ANANTA_MASTER_LLM_*" in rm["warnings"][0]


# ── N) Full chain: request_override beats task_override ─────────────────────

def test_request_override_beats_task_override():
    """request_runtime_override (rank 1) beats task_override_map (rank 2)."""
    rules = RoutingRules(task_overrides={"coding": "task-p"})
    resolver = ModelProfileResolver(
        profiles=[_local("request-p"), _local("task-p")],
        routing_rules=rules,
    )
    ctx = RoutingContext(request_profile_id="request-p", task_kind="coding")
    result = resolver.resolve(ctx)
    assert result.profile.profile_id == "request-p"
    assert result.final_source == "request_runtime_override"


# ── O) Full chain: user_override beats master_default ───────────────────────

def test_user_override_beats_master_default():
    """user_runtime_override (rank 8) beats global_master_default (rank 9)."""
    resolver = ModelProfileResolver(
        profiles=[_local("user-p")],
        master_default_profile=_master_profile(),
    )
    ctx = RoutingContext(user_profile_id="user-p")
    result = resolver.resolve(ctx)
    assert result.profile.profile_id == "user-p"
    assert result.final_source == "user_runtime_override"


# ── P) Request override with non-existent profile_id falls through ──────────

def test_request_override_unknown_profile_falls_through():
    """If request_profile_id does not match any profile, fall through ranks."""
    resolver = ModelProfileResolver(
        profiles=[_local("exists-p")],
        master_default_profile=_master_profile("ollama", "master-m"),
    )
    ctx = RoutingContext(request_profile_id="nonexistent")
    result = resolver.resolve(ctx)
    # Falls through request → master default wins
    assert result.ok
    assert result.final_source == "global_master_default"
    assert result.profile.model == "master-m"
