"""Tests for ModelProfileResolver — AMR-008."""
import pytest
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import (
    ModelProfileResolver,
    ProviderHealthCache,
    RoutingContext,
    RoutingRules,
    SecurityPolicyChecker,
)


def _local(profile_id: str, model_role: str = "any", **kwargs) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="ollama",
        model="qwen:7b",
        model_role=model_role,
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
        **kwargs,
    )


def _cloud(profile_id: str, **kwargs) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="openai",
        model="gpt-4o",
        model_role="planner",
        local=False,
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        **kwargs,
    )


# ── rank 2: blueprint rule ────────────────────────────────────────────────────

def test_blueprint_rule_resolves():
    rules = RoutingRules(blueprint_rules={"coding": "local-coder"})
    resolver = ModelProfileResolver(
        profiles=[_local("local-coder")],
        routing_rules=rules,
    )
    ctx = RoutingContext(blueprint_id="coding")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "local-coder"
    assert result.final_source == "blueprint_rule"


# ── rank 6: model_role rule ───────────────────────────────────────────────────

def test_role_rule_resolves():
    rules = RoutingRules(role_rules={"coder": "local-coder"})
    resolver = ModelProfileResolver(
        profiles=[_local("local-coder", model_role="coder")],
        routing_rules=rules,
    )
    ctx = RoutingContext(model_role="coder")
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "local-coder"
    assert result.final_source == "model_role_rule"


# ── rank 10: capability match ─────────────────────────────────────────────────

def test_capability_match_picks_first_enabled():
    resolver = ModelProfileResolver(profiles=[_local("p1"), _local("p2")])
    ctx = RoutingContext()
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "p1"
    assert result.final_source == "capability_match"


def test_capability_match_skips_disabled():
    p_disabled = _local("disabled", enabled=False)
    p_enabled = _local("enabled")
    resolver = ModelProfileResolver(profiles=[p_disabled, p_enabled])
    ctx = RoutingContext()
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "enabled"


def test_capability_match_requires_tools():
    p_no_tools = _local("p_no_tools", supports_tools=False)
    p_tools = _local("p_tools", supports_tools=True)
    resolver = ModelProfileResolver(profiles=[p_no_tools, p_tools])
    ctx = RoutingContext(requires_tools=True)
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "p_tools"


def test_capability_match_requires_json():
    p_no_json = _local("p_no_json", supports_json=False)
    p_json = _local("p_json", supports_json=True)
    resolver = ModelProfileResolver(profiles=[p_no_json, p_json])
    ctx = RoutingContext(requires_json=True)
    result = resolver.resolve(ctx)
    assert result.ok
    assert result.profile.profile_id == "p_json"


# ── rank 0: security policy ───────────────────────────────────────────────────

def test_security_blocks_cloud_when_secrets_present():
    cloud_p = _cloud("cloud-p")
    resolver = ModelProfileResolver(
        profiles=[cloud_p],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    ctx = RoutingContext(context_text="api_key=sk-supersecret1234567890")
    result = resolver.resolve(ctx)
    assert not result.ok
    assert any("security_policy" in r for _, r in result.blocked_candidates)


def test_dry_run_security_and_context_facts_do_not_require_raw_prompt():
    cloud_p = _cloud("cloud-p", context_tokens=100, max_output_tokens=20)
    resolver = ModelProfileResolver(
        profiles=[cloud_p],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    secret_result = resolver.resolve(RoutingContext(
        allow_cloud=True, contains_secrets=True, approximate_context_tokens=10,
    ))
    assert not secret_result.ok
    assert ("cloud-p", "security_policy:secrets_declared_cloud_blocked") in secret_result.blocked_candidates

    oversized_result = resolver.resolve(RoutingContext(
        allow_cloud=True, contains_secrets=False, approximate_context_tokens=81,
    ))
    assert not oversized_result.ok
    assert any(item.reason == "capability:context_too_large" for item in oversized_result.decisions)


def test_security_allows_cloud_without_secrets():
    cloud_p = _cloud("cloud-p")
    resolver = ModelProfileResolver(
        profiles=[cloud_p],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
    )
    ctx = RoutingContext(
        context_text="just normal text about python",
        allow_cloud=True,
    )
    result = resolver.resolve(ctx)
    assert result.ok


def test_security_blocks_cloud_without_cloud_allowed():
    p = ModelProfile(
        profile_id="cloud-no-allowed",
        provider_id="openai",
        model="gpt-4",
        cloud=True,
        cloud_allowed=False,
        block_secret_context=True,
    )
    resolver = ModelProfileResolver(profiles=[p])
    result = resolver.resolve(RoutingContext())
    assert not result.ok


def test_security_blocks_provider_not_in_allowlist():
    cloud_p = _cloud("cloud-openai")
    resolver = ModelProfileResolver(
        profiles=[cloud_p],
        security_policy=SecurityPolicyChecker(allowed_cloud_providers=["openrouter"]),
    )
    ctx = RoutingContext()
    result = resolver.resolve(ctx)
    assert not result.ok


# ── rank 1: task_kind override ────────────────────────────────────────────────

def test_task_override_takes_precedence_over_role():
    rules = RoutingRules(
        task_overrides={"review": "p-review"},
        role_rules={"reviewer": "p-role-reviewer"},
    )
    resolver = ModelProfileResolver(
        profiles=[_local("p-review"), _local("p-role-reviewer")],
        routing_rules=rules,
    )
    ctx = RoutingContext(task_kind="review", model_role="reviewer")
    result = resolver.resolve(ctx)
    assert result.profile.profile_id == "p-review"
    assert result.final_source == "task_override_map"


def test_explicit_rule_skips_unhealthy_provider():
    cache = ProviderHealthCache()
    cache.mark_unavailable("ollama")
    rules = RoutingRules(
        global_profile_id="p-ollama",
        fallback_chain=["p-lmstudio"],
    )
    p_ollama = _local("p-ollama")
    p_lmstudio = ModelProfile(
        profile_id="p-lmstudio",
        provider_id="lmstudio",
        model="m",
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
    )
    resolver = ModelProfileResolver(
        profiles=[p_ollama, p_lmstudio],
        routing_rules=rules,
        health_cache=cache,
    )

    result = resolver.resolve(RoutingContext())

    assert result.ok
    assert result.profile.profile_id == "p-lmstudio"
    assert any(
        d.profile_id == "p-ollama" and d.reason == "provider_health:unavailable:ollama"
        for d in result.decisions
    )


# ── rank 11: fallback chain ───────────────────────────────────────────────────

def test_fallback_chain_used_when_capability_match_fails():
    # profile requires tools but ctx doesn't → capability_match blocks it
    # put same profile in fallback chain so fallback resolves without capability check on fallback
    # Actually fallback chain also runs _try() which enforces security but not capability.
    # Use a profile that passes security but fails capability for rank 10, then put it in fallback
    # to verify fallback chain runs. Here we make the profile support tools so fallback accepts it
    # but capability_match already found it — instead, put an *excluded* profile in capability pool
    # and a different one only in fallback chain.
    p_requires_tools = _local("p-tools-required", supports_tools=False)
    p_fallback = _local("p-fallback", supports_tools=True)
    # p_fallback is NOT in profiles list (so resolver can't pick it via capability_match)
    # only in fallback_chain. We simulate by having only p_requires_tools in profiles
    # and requiring tools → capability_match fails → falls back.
    rules = RoutingRules(fallback_chain=["p-fallback"])
    resolver = ModelProfileResolver(
        profiles=[p_requires_tools, p_fallback],
        routing_rules=rules,
    )
    # No rule match, and require tools=True: p_requires_tools can't pass, p_fallback CAN
    ctx = RoutingContext(requires_tools=True)
    result = resolver.resolve(ctx)
    assert result.ok
    # p_fallback supports tools so capability_match picks it at rank 10
    assert result.profile.profile_id == "p-fallback"


def test_no_profiles_returns_failed_result():
    resolver = ModelProfileResolver(profiles=[])
    result = resolver.resolve(RoutingContext())
    assert not result.ok
    assert result.profile is None


def test_result_summary_format():
    resolver = ModelProfileResolver(profiles=[_local("p1")])
    result = resolver.resolve(RoutingContext())
    summary = result.summary()
    assert "resolved:p1" in summary
    assert "capability_match" in summary


def test_decision_trace_is_populated():
    resolver = ModelProfileResolver(profiles=[_local("p1")])
    result = resolver.resolve(RoutingContext())
    assert len(result.decisions) > 0
    accepted = [d for d in result.decisions if d.accepted]
    assert len(accepted) == 1
    assert accepted[0].profile_id == "p1"


def test_fallback_group_returns_local_gemma_qwen_chain():
    local = ModelProfile(
        profile_id="local_lmstudio_phi_json_worker",
        provider_id="lmstudio",
        model="auto",
        local=True,
        block_secret_context=False,
        supports_json=True,
        tool_calling_mode="prompt_json",
        fallback_group="local_first_cheap",
        fallback_rank=10,
    )
    gemma = ModelProfile(
        profile_id="openrouter_gemma3_4b_cheap_json",
        provider_id="openrouter",
        model="google/gemma-3-4b-it",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        supports_json=True,
        supports_tools=True,
        tool_calling_mode="both",
        fallback_group="local_first_cheap",
        fallback_rank=20,
    )
    qwen = ModelProfile(
        profile_id="openrouter_qwen3_30b_a3b_stronger",
        provider_id="openrouter",
        model="qwen/qwen3-30b-a3b-instruct-2507",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        supports_json=True,
        supports_tools=True,
        tool_calling_mode="both",
        fallback_group="local_first_cheap",
        fallback_rank=30,
    )
    rules = RoutingRules.from_dict({
        "fallback_groups": {
            "local_first_cheap": {
                "ordered_profiles": [local.profile_id, gemma.profile_id, qwen.profile_id]
            }
        }
    })
    resolver = ModelProfileResolver([local, gemma, qwen], routing_rules=rules)
    result, chain = resolver.resolve_candidate_chain(RoutingContext(
        fallback_group_id="local_first_cheap",
        requires_tools=True,
        requires_json=True,
        allow_cloud=True,
    ))

    assert result.ok
    assert [p.profile_id for p in chain] == [
        "local_lmstudio_phi_json_worker",
        "openrouter_gemma3_4b_cheap_json",
        "openrouter_qwen3_30b_a3b_stronger",
    ]


def test_secret_context_blocks_cloud_candidates_but_keeps_local():
    local = _local("local", supports_json=True)
    gemma = ModelProfile(
        profile_id="gemma",
        provider_id="openrouter",
        model="google/gemma-3-4b-it",
        cloud=True,
        cloud_allowed=True,
        block_secret_context=True,
        supports_json=True,
        fallback_group="g",
        fallback_rank=20,
    )
    rules = RoutingRules.from_dict({"fallback_groups": {"g": {"ordered_profiles": ["local", "gemma"]}}})
    resolver = ModelProfileResolver([local, gemma], routing_rules=rules)

    result, chain = resolver.resolve_candidate_chain(RoutingContext(
        fallback_group_id="g",
        context_text="OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz",
        allow_cloud=True,
    ))

    assert result.ok
    assert [p.profile_id for p in chain] == ["local"]
    assert any(pid == "gemma" and "secrets_detected" in reason for pid, reason in result.blocked_candidates)


def test_fallback_group_cost_cap_is_the_stricter_routing_context_limit():
    expensive = _local(
        "expensive",
        fallback_group="local",
        price_input_per_million=1_000_000.0,
        price_output_per_million=1_000_000.0,
        max_output_tokens=1,
    )
    free = _local(
        "free",
        fallback_group="local",
        price_input_per_million=0.0,
        price_output_per_million=0.0,
        max_output_tokens=1,
    )
    resolver = ModelProfileResolver(
        [expensive, free],
        routing_rules=RoutingRules.from_dict(
            {
                "fallback_groups": {
                    "local": {
                        "ordered_profiles": ["expensive", "free"],
                        "max_total_retries": 0,
                        "cost_policy": {
                            "max_estimated_cost_per_step": 1.0,
                        },
                    }
                }
            }
        ),
    )
    ctx = RoutingContext(
        fallback_group_id="local",
        context_text="four",
        max_estimated_cost_per_step=5.0,
    )

    result, chain = resolver.resolve_candidate_chain(ctx)

    assert resolver.effective_max_estimated_cost_per_step(ctx) == 1.0
    assert result.ok
    assert result.profile.profile_id == "free"
    assert [profile.profile_id for profile in chain] == ["free"]
    assert any(
        decision.profile_id == "expensive"
        and decision.reason == "policy:estimated_cost_per_step_exceeded"
        for decision in result.decisions
    )


def test_local_phi_gemma_group_budget_matches_profile_retry_budgets():
    import json
    from pathlib import Path

    from agent.services.model_profile_loader import ModelProfileLoader

    root = Path(__file__).resolve().parents[1]
    routing = json.loads(
        (
            root
            / "config/models/local-ollama-phi-gemma-rtx3080.model_routing.json"
        ).read_text(encoding="utf-8")
    )
    profiles = ModelProfileLoader().load_file(
        root
        / "config/models/local-ollama-phi-gemma-rtx3080.model_profiles.yaml"
    )
    rules = RoutingRules.from_dict(routing, strict=True)

    assert profiles.ok, profiles.errors
    group_id = "local_phi_to_gemma_reasoning"
    profile_retry_budget = sum(
        profile.retry_budget
        for profile in profiles.profiles
        if profile.fallback_group == group_id
    )
    assert profile_retry_budget == 3
    assert rules.fallback_groups[group_id].max_total_retries == profile_retry_budget
    repeated_failure_rule = next(
        rule
        for rule in rules.escalation_rules
        if rule.trigger == "repeated_failure"
    )
    assert repeated_failure_rule.from_profile == "local_ollama_phi4_mini"
    assert repeated_failure_rule.to_profile == (
        "local_ollama_gemma4_e4b_reasoning"
    )
    assert repeated_failure_rule.condition["repeated_failure_count"] == 3

    resolver = ModelProfileResolver(
        profiles.profiles,
        routing_rules=rules,
    )
    result, chain = resolver.resolve_candidate_chain(
        RoutingContext(
            model_role="reasoning",
            fallback_group_id=group_id,
        )
    )
    assert result.ok
    assert result.profile.profile_id == "local_ollama_phi4_mini"
    assert [profile.profile_id for profile in chain] == [
        "local_ollama_phi4_mini",
        "local_ollama_gemma4_e4b_reasoning",
    ]
    gemma = next(
        profile
        for profile in profiles.profiles
        if profile.profile_id == "local_ollama_gemma4_e4b_reasoning"
    )
    assert gemma.max_input_tokens() == (
        8192 - 3072 - gemma.system_prompt_prefix_tokens()
    )


def test_profile_context_check_reserves_configured_completion_tokens():
    profile = _local(
        "bounded",
        context_tokens=100,
        max_context_for_profile=100,
        max_output_tokens=20,
    )
    resolver = ModelProfileResolver([profile])

    accepted = resolver.resolve(
        RoutingContext(context_text="x" * (80 * 4))
    )
    rejected = resolver.resolve(
        RoutingContext(context_text="x" * (80 * 4 + 1))
    )

    assert accepted.ok
    assert not rejected.ok
    assert any(
        decision.reason == "capability:context_too_large"
        for decision in rejected.decisions
    )


# ── T02 — Token budget extension fields ──────────────────────────────────────

def test_new_fields_safe_defaults_on_local_profile():
    """Legacy profiles without new fields have safe defaults."""
    p = _local("x")
    assert p.context_window_tokens is None
    assert p.hard_max_output_tokens is None
    assert p.tokenizer_strategy == "chars_per_token"
    assert p.tokenizer_name is None
    assert p.input_cost_per_1m_tokens is None
    assert p.output_cost_per_1m_tokens is None


def test_new_fields_loaded_via_loader():
    from agent.services.model_profile_loader import ModelProfileLoader
    loader = ModelProfileLoader()
    result = loader.load_dict({"profiles": [{
        "profile_id": "budget-test",
        "provider_id": "ollama",
        "model": "llama3",
        "context_window_tokens": 65536,
        "hard_max_output_tokens": 8192,
        "tokenizer_strategy": "tiktoken_cl100k",
        "tokenizer_name": "cl100k_base",
        "input_cost_per_1m_tokens": 0.0,
        "output_cost_per_1m_tokens": 0.0,
    }]})
    assert not result.errors
    p = result.profiles[0]
    assert p.context_window_tokens == 65536
    assert p.hard_max_output_tokens == 8192
    assert p.tokenizer_strategy == "tiktoken_cl100k"
    assert p.tokenizer_name == "cl100k_base"


def test_new_fields_do_not_break_cloud_profile_validation():
    from agent.services.model_profile_loader import ModelProfileLoader
    loader = ModelProfileLoader()
    result = loader.load_dict({"profiles": [{
        "profile_id": "cloud-with-budget",
        "provider_id": "openai",
        "model": "gpt-4o",
        "cloud": True,
        "cloud_allowed": True,
        "block_secret_context": True,
        "input_cost_per_1m_tokens": 5.0,
        "output_cost_per_1m_tokens": 15.0,
    }]})
    assert not result.errors
    p = result.profiles[0]
    assert p.input_cost_per_1m_tokens == pytest.approx(5.0)
    assert p.output_cost_per_1m_tokens == pytest.approx(15.0)
