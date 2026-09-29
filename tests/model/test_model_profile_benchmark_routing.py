import pytest
from agent.llm_benchmarks import record_benchmark_sample, recommend_profiles_for_context
from agent.services.model_profile_loader import ModelProfile
from agent.services.model_profile_resolver import ModelProfileResolver, RoutingContext, SecurityPolicyChecker


def _profile(profile_id: str, *, provider: str = "ollama", latency: int = 100) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id=provider,
        model=f"{profile_id}-model",
        model_role="coder",
        local=provider not in {"openai", "openrouter"},
        cloud=provider in {"openai", "openrouter"},
        cloud_allowed=provider in {"openai", "openrouter"},
        block_secret_context=provider in {"openai", "openrouter"},
        supports_json=True,
        extra={"test_latency": latency},
    )


def test_benchmark_prefers_faster_profile_within_allowed_profiles(tmp_path):
    for _ in range(3):
        record_benchmark_sample(
            data_dir=str(tmp_path),
            agent_cfg={},
            provider="ollama",
            model="slow-model",
            profile_id="slow",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=4000,
            tokens_total=1000,
        )
        record_benchmark_sample(
            data_dir=str(tmp_path),
            agent_cfg={},
            provider="ollama",
            model="fast-model",
            profile_id="fast",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=250,
            tokens_total=1000,
        )

    ranked = recommend_profiles_for_context(
        data_dir=str(tmp_path),
        task_kind="coding",
        allowed_profile_ids=["slow", "fast"],
        min_samples=3,
    )
    resolver = ModelProfileResolver(
        profiles=[_profile("slow"), _profile("fast")],
        benchmark_profile_order=[row["profile_id"] for row in ranked],
        benchmark_metadata={"sample_count": sum(row["sample_count"] for row in ranked)},
    )

    result = resolver.resolve(RoutingContext(model_role="coder", task_kind="coding"))

    assert ranked[0]["profile_id"] == "fast"
    assert result.ok
    assert result.profile.profile_id == "fast"
    assert any(decision.source == "benchmark_profile_ranking" for decision in result.decisions)
    assert resolver.benchmark_ranking_read_model()["active"] is True


def test_benchmark_ranking_cannot_override_secret_cloud_policy(tmp_path):
    for _ in range(3):
        record_benchmark_sample(
            data_dir=str(tmp_path),
            agent_cfg={},
            provider="openai",
            model="gpt-4o",
            profile_id="cloud-fast",
            task_kind="coding",
            success=True,
            quality_gate_passed=True,
            latency_ms=10,
            tokens_total=100,
        )
    ranked = recommend_profiles_for_context(
        data_dir=str(tmp_path),
        task_kind="coding",
        allowed_profile_ids=["cloud-fast", "local-safe"],
        min_samples=3,
    )
    resolver = ModelProfileResolver(
        profiles=[_profile("cloud-fast", provider="openai"), _profile("local-safe")],
        security_policy=SecurityPolicyChecker(block_cloud_with_secrets=True),
        benchmark_profile_order=[row["profile_id"] for row in ranked],
    )

    result = resolver.resolve(
        RoutingContext(
            model_role="coder",
            task_kind="coding",
            context_text="api_key=sk-supersecret1234567890",
        )
    )

    assert result.ok
    assert result.profile.profile_id == "local-safe"
    assert any(profile_id == "cloud-fast" for profile_id, _reason in result.blocked_candidates)


# ── T07 — RoutingDecisionChain + cost estimation ──────────────────────────────

def _routing_profile(profile_id: str, cost_class: str = "free", **kwargs) -> ModelProfile:
    return ModelProfile(
        profile_id=profile_id,
        provider_id="ollama",
        model=f"model-{profile_id}",
        local=True,
        cloud=False,
        cloud_allowed=False,
        block_secret_context=False,
        cost_class=cost_class,
        **kwargs,
    )


def test_routing_decision_chain_with_context_budget():
    from agent.services.routing_decision_service import RoutingDecisionService
    from unittest.mock import MagicMock

    svc = RoutingDecisionService()
    mock_budget = MagicMock()
    mock_budget.decision_ref = "budget-ref-123"
    mock_budget.mode = "project_chat"

    result = svc.build_decision_chain(
        cfg=None,
        task_kind="coding",
        requested={},
        effective={},
        sources={},
        context_budget=mock_budget,
    )
    assert result["context_budget_decision_ref"] == "budget-ref-123"
    assert result["token_budget_note"] == "project_chat"


def test_routing_decision_chain_no_context_budget():
    from agent.services.routing_decision_service import RoutingDecisionService

    svc = RoutingDecisionService()
    result = svc.build_decision_chain(
        cfg=None,
        task_kind="coding",
        requested={},
        effective={},
        sources={},
    )
    assert "context_budget_decision_ref" not in result or result.get("context_budget_decision_ref") is None


def test_estimate_cost_eur_with_cost_field():
    from agent.services.routing_decision_service import estimate_cost_eur

    profile = _routing_profile("cheap", input_cost_per_1m_tokens=0.5)
    cost = estimate_cost_eur(1_000_000, profile)
    assert cost is not None
    assert abs(cost - 0.5) < 1e-9


def test_estimate_cost_eur_free_profile_no_cost():
    from agent.services.routing_decision_service import estimate_cost_eur

    profile = _routing_profile("free-local")
    cost = estimate_cost_eur(1_000_000, profile)
    assert cost is None  # no cost field set


def test_estimate_cost_eur_fallback_to_price_input_per_million():
    from agent.services.routing_decision_service import estimate_cost_eur

    profile = _routing_profile("legacy", price_input_per_million=1.0)
    cost = estimate_cost_eur(500_000, profile)
    assert cost is not None
    assert abs(cost - 0.5) < 1e-9


def test_estimate_cost_eur_none_profile():
    from agent.services.routing_decision_service import estimate_cost_eur

    assert estimate_cost_eur(1000, None) is None


def test_free_to_cheap_to_expensive_routing_with_budget_gate():
    """Simulate free→cheap→expensive routing logic with profile costs."""
    from agent.services.routing_decision_service import estimate_cost_eur

    free_profile = _routing_profile("free-local", cost_class="free")
    cheap_profile = _routing_profile("cheap-cloud", cost_class="low", input_cost_per_1m_tokens=0.1)
    expensive_profile = _routing_profile("expensive-cloud", cost_class="high", input_cost_per_1m_tokens=10.0)

    tokens = 100_000
    costs = {
        p.profile_id: estimate_cost_eur(tokens, p)
        for p in [free_profile, cheap_profile, expensive_profile]
    }
    assert costs["free-local"] is None  # free, no cost data
    assert costs["cheap-cloud"] == pytest.approx(0.01)
    assert costs["expensive-cloud"] == pytest.approx(1.0)
    # Ensure cheap is cheaper than expensive
    assert costs["cheap-cloud"] < costs["expensive-cloud"]
