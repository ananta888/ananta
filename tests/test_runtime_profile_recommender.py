from __future__ import annotations

from agent.services.runtime_profile_recommender import RuntimeRecommendationRequest, recommend_runtime_profile


def test_runtime_profile_recommender_cpu_only_defaults_are_conservative() -> None:
    recommendation = recommend_runtime_profile(RuntimeRecommendationRequest(environment="cpu-only"))
    assert recommendation.provider == "ollama"
    assert recommendation.context_window_tokens == 32000
    assert recommendation.max_input_tokens == 8000
    assert recommendation.max_output_tokens == 1024
    assert recommendation.rag_budget_tokens == 12000
    assert recommendation.patch_size_lines == 120
    assert recommendation.requires_explicit_provider_config is False


def test_runtime_profile_recommender_nvidia_gpu_recommendation() -> None:
    recommendation = recommend_runtime_profile(RuntimeRecommendationRequest(environment="nvidia-gpu"))
    assert recommendation.provider == "ollama"
    assert recommendation.model == "qwen2.5-coder:14b"
    assert recommendation.context_window_tokens == 64000
    assert recommendation.max_output_tokens == 2048
    assert recommendation.patch_size_lines >= 180


def test_runtime_profile_recommender_remote_openai_compatible_with_explicit_endpoint() -> None:
    recommendation = recommend_runtime_profile(
        RuntimeRecommendationRequest(
            environment="remote-model",
            explicit_remote_endpoint="http://remote-hub.example/v1",
        )
    )
    assert recommendation.provider == "openai-compatible"
    assert recommendation.remote_execution_weight > recommendation.local_execution_weight
    assert recommendation.requires_explicit_provider_config is False


def test_runtime_profile_recommender_never_enables_paid_provider_silently() -> None:
    recommendation = recommend_runtime_profile(RuntimeRecommendationRequest(environment="remote-model"))
    assert recommendation.provider == "ollama"
    assert recommendation.requires_explicit_provider_config is True
    assert any("never enabled silently" in note for note in recommendation.notes)

