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
