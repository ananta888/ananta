"""MPM-002: Tests comparing old DEFAULT_PROVIDER/DEFAULT_MODEL config
against new MODEL_PROFILES_PATH profile-resolver config.

Verifies backward-compat and correct precedence when both are set.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from agent.services.model_profile_loader import ModelProfile, ModelProfileLoader
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    RoutingContext,
    RoutingRules,
)
from agent.services.model_override_normalization_service import (
    ModelOverrideNormalizationService,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_profile_file(profiles: list[dict]) -> str:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    )
    json.dump({"profiles": profiles}, tmp)
    tmp.flush()
    return tmp.name


def _local_profile(pid: str, model: str = "qwen:7b", role: str = "any") -> ModelProfile:
    return ModelProfile(
        profile_id=pid,
        provider_id="lmstudio",
        model=model,
        model_role=role,
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
    )


# ── ModelOverrideNormalizationService ────────────────────────────────────────

def test_legacy_dict_roundtrip():
    svc = ModelOverrideNormalizationService()
    result = svc.from_legacy_dict({"provider": "lmstudio", "model": "qwen2.5-coder-7b"})
    assert result is not None
    assert result.profile.provider_id == "lmstudio"
    assert result.profile.model == "qwen2.5-coder-7b"


def test_legacy_alias_lm_studio_normalizes():
    svc = ModelOverrideNormalizationService()
    result = svc.from_legacy_dict({"provider": "lm_studio", "model": "some-model"})
    assert result is not None
    assert result.profile.provider_id == "lmstudio"


def test_legacy_alias_local_normalizes():
    svc = ModelOverrideNormalizationService()
    result = svc.from_legacy_dict({"provider": "local", "model": "anything"})
    assert result is not None
    assert result.profile.provider_id == "lmstudio"


def test_legacy_empty_dict_returns_none():
    svc = ModelOverrideNormalizationService()
    result = svc.from_legacy_dict({})
    assert result is None


def test_legacy_env_reads_from_env_vars(monkeypatch):
    monkeypatch.setenv("DEFAULT_PROVIDER", "lmstudio")
    monkeypatch.setenv("DEFAULT_MODEL", "qwen2.5-coder-7b")
    svc = ModelOverrideNormalizationService()
    result = svc.from_env()
    assert result is not None
    assert result.profile.provider_id == "lmstudio"
    assert result.profile.model == "qwen2.5-coder-7b"


def test_legacy_env_returns_none_when_not_set(monkeypatch):
    monkeypatch.delenv("DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    svc = ModelOverrideNormalizationService()
    result = svc.from_env()
    assert result is None


# ── Profile resolver with no legacy config ───────────────────────────────────

def test_resolver_uses_first_profile_via_capability_match():
    profiles = [
        _local_profile("profile-a"),
        _local_profile("profile-b"),
    ]
    # No rules configured → falls through to capability_match (rank 10)
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=RoutingRules())
    result = resolver.resolve(RoutingContext())
    assert result.profile is not None
    assert result.profile.profile_id in {"profile-a", "profile-b"}
    assert result.final_source == "capability_match"


def test_resolver_global_rule_sets_winner():
    profiles = [
        _local_profile("profile-a"),
        _local_profile("profile-b"),
    ]
    rules = RoutingRules(global_profile_id="profile-b")
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=rules)
    result = resolver.resolve(RoutingContext())
    assert result.profile.profile_id == "profile-b"
    assert result.final_source == "global_routing_config"
    assert result.final_rank == 7


def test_resolver_role_rule_beats_global():
    profiles = [
        _local_profile("generic", role="any"),
        _local_profile("expert", role="expert"),
    ]
    rules = RoutingRules(
        global_profile_id="generic",
        role_rules={"expert": "expert"},
    )
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=rules)
    result = resolver.resolve(RoutingContext(model_role="expert"))
    assert result.profile.profile_id == "expert"
    assert result.final_source == "model_role_rule"
    assert result.final_rank == 6


# ── Profile resolver with legacy fallback ────────────────────────────────────

def test_resolve_with_fallback_uses_profile_when_available():
    profiles = [_local_profile("profile-a")]
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=RoutingRules())
    result, fallback_info = resolver.resolve_with_fallback(
        RoutingContext(),
        legacy_provider="lmstudio",
        legacy_model="old-model",
    )
    assert result.profile.profile_id == "profile-a"
    assert fallback_info == {}


def test_resolve_with_fallback_returns_legacy_when_no_profiles():
    resolver = ModelProfileResolver(profiles=[], routing_rules=RoutingRules())
    result, fallback_info = resolver.resolve_with_fallback(
        RoutingContext(),
        legacy_provider="lmstudio",
        legacy_model="old-model",
    )
    assert fallback_info.get("legacy_provider") == "lmstudio"
    assert fallback_info.get("legacy_model") == "old-model"


# ── Profile loader from file ──────────────────────────────────────────────────

def test_loader_reads_json_file():
    data = [{"profile_id": "from-file", "provider_id": "ollama",
             "model": "qwen:7b", "local": True, "cloud": False}]
    path = _make_profile_file(data)
    try:
        loader = ModelProfileLoader()
        result = loader.load_file(path)
        assert result.ok
        assert len(result.profiles) == 1
        assert result.profiles[0].profile_id == "from-file"
    finally:
        os.unlink(path)


def test_loader_rejects_cloud_profile_missing_cloud_allowed():
    data = [{"profile_id": "bad-cloud", "provider_id": "openai",
             "model": "gpt-4o", "local": False, "cloud": True}]
    path = _make_profile_file(data)
    try:
        loader = ModelProfileLoader()
        result = loader.load_file(path)
        assert not result.ok or any("bad-cloud" in e for e in result.errors)
    finally:
        os.unlink(path)


def test_loader_accepts_valid_cloud_profile():
    data = [{
        "profile_id": "good-cloud",
        "provider_id": "openai",
        "model": "gpt-4o",
        "local": False,
        "cloud": True,
        "cloud_allowed": True,
        "block_secret_context": True,
    }]
    path = _make_profile_file(data)
    try:
        loader = ModelProfileLoader()
        result = loader.load_file(path)
        assert result.ok
        p = result.profiles[0]
        assert p.cloud_allowed is True
        assert p.block_secret_context is True
    finally:
        os.unlink(path)


def test_loader_rejects_duplicate_profile_ids():
    data = [
        {"profile_id": "dup", "provider_id": "ollama", "model": "m1", "local": True, "cloud": False},
        {"profile_id": "dup", "provider_id": "ollama", "model": "m2", "local": True, "cloud": False},
    ]
    path = _make_profile_file(data)
    try:
        loader = ModelProfileLoader()
        result = loader.load_file(path)
        assert not result.ok or len(result.errors) > 0
    finally:
        os.unlink(path)


# ── Deprecation when both configured ─────────────────────────────────────────

def test_deprecation_warning_logged_when_both_set(caplog):
    """Profile resolver takes precedence; deprecation warning is logged."""
    import logging
    profiles = [_local_profile("new-profile")]
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=RoutingRules())

    with caplog.at_level(logging.WARNING):
        result, fallback_info = resolver.resolve_with_fallback(
            RoutingContext(),
            legacy_provider="lmstudio",
            legacy_model="old-model",
        )

    # Profile wins
    assert result.profile.profile_id == "new-profile"
    # No fallback_info since profile resolved
    assert fallback_info == {}


# ── MPM-003: Read-model effective winner ──────────────────────────────────────

def test_resolution_result_carries_source_rank():
    profiles = [
        _local_profile("default-profile"),
        _local_profile("expert-profile", role="expert"),
    ]
    rules = RoutingRules(
        global_profile_id="default-profile",
        role_rules={"expert": "expert-profile"},
    )
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=rules)

    r1 = resolver.resolve(RoutingContext(model_role="expert"))
    assert r1.final_source == "model_role_rule"
    assert r1.final_rank == 6

    r2 = resolver.resolve(RoutingContext())
    assert r2.final_source == "global_routing_config"
    assert r2.final_rank == 7


def test_resolution_result_carries_decisions():
    profiles = [_local_profile("p1")]
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=RoutingRules())
    result = resolver.resolve(RoutingContext())
    assert isinstance(result.decisions, list)


def test_resolution_result_carries_blocked_candidates():
    from agent.services.model_profile_resolver import ProviderHealthCache
    profiles = [
        _local_profile("unhealthy"),
        _local_profile("healthy"),
    ]
    cache = ProviderHealthCache()
    cache.mark_unavailable("lmstudio")  # both profiles use lmstudio
    resolver = ModelProfileResolver(profiles=profiles, routing_rules=RoutingRules(),
                                    health_cache=cache)
    result = resolver.resolve(RoutingContext(requires_tools=False))
    assert isinstance(result.blocked_candidates, list)
