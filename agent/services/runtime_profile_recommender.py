from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EnvironmentKind = Literal["cpu-only", "nvidia-gpu", "remote-model", "mixed-local-remote"]


@dataclass(frozen=True)
class RuntimeRecommendationRequest:
    environment: EnvironmentKind
    allow_paid_providers: bool = False
    explicit_remote_endpoint: str | None = None


@dataclass(frozen=True)
class RuntimeRecommendation:
    environment: EnvironmentKind
    runtime_profile: str
    governance_mode: str
    provider: str
    model: str
    context_window_tokens: int
    max_input_tokens: int
    max_output_tokens: int
    rag_budget_tokens: int
    patch_size_lines: int
    local_execution_weight: float
    remote_execution_weight: float
    requires_explicit_provider_config: bool
    notes: tuple[str, ...]


def _conservative_recommendation(
    *,
    environment: EnvironmentKind,
    provider: str,
    model: str,
    context_window_tokens: int,
    max_input_tokens: int,
    max_output_tokens: int,
    rag_budget_tokens: int,
    patch_size_lines: int,
    local_execution_weight: float,
    remote_execution_weight: float,
    requires_explicit_provider_config: bool,
    notes: tuple[str, ...],
) -> RuntimeRecommendation:
    return RuntimeRecommendation(
        environment=environment,
        runtime_profile="local-dev" if environment in {"cpu-only", "nvidia-gpu"} else "compose-safe",
        governance_mode="safe" if environment in {"cpu-only", "nvidia-gpu"} else "balanced",
        provider=provider,
        model=model,
        context_window_tokens=context_window_tokens,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        rag_budget_tokens=rag_budget_tokens,
        patch_size_lines=patch_size_lines,
        local_execution_weight=local_execution_weight,
        remote_execution_weight=remote_execution_weight,
        requires_explicit_provider_config=requires_explicit_provider_config,
        notes=notes,
    )


def recommend_runtime_profile(request: RuntimeRecommendationRequest) -> RuntimeRecommendation:
    env = request.environment
    has_explicit_remote = bool(str(request.explicit_remote_endpoint or "").strip())

    if env == "cpu-only":
        return _conservative_recommendation(
            environment=env,
            provider="ollama",
            model="qwen2.5-coder:7b",
            context_window_tokens=32000,
            max_input_tokens=8000,
            max_output_tokens=1024,
            rag_budget_tokens=12000,
            patch_size_lines=120,
            local_execution_weight=1.0,
            remote_execution_weight=0.0,
            requires_explicit_provider_config=False,
            notes=(
                "CPU-only profile keeps output and patch sizes conservative.",
                "No paid/cloud provider is selected automatically.",
            ),
        )

    if env == "nvidia-gpu":
        return _conservative_recommendation(
            environment=env,
            provider="ollama",
            model="qwen2.5-coder:14b",
            context_window_tokens=64000,
            max_input_tokens=16000,
            max_output_tokens=2048,
            rag_budget_tokens=32000,
            patch_size_lines=220,
            local_execution_weight=1.0,
            remote_execution_weight=0.0,
            requires_explicit_provider_config=False,
            notes=(
                "NVIDIA GPU profile raises context and patch limits conservatively.",
                "No paid/cloud provider is selected automatically.",
            ),
        )

    if env == "remote-model":
        if has_explicit_remote or request.allow_paid_providers:
            return _conservative_recommendation(
                environment=env,
                provider="openai-compatible",
                model="model",
                context_window_tokens=64000,
                max_input_tokens=24000,
                max_output_tokens=2048,
                rag_budget_tokens=32000,
                patch_size_lines=180,
                local_execution_weight=0.2,
                remote_execution_weight=0.8,
                requires_explicit_provider_config=not has_explicit_remote,
                notes=(
                    "Remote recommendation requires explicit endpoint/API-key configuration.",
                    "Cloud/provider billing is never enabled silently.",
                ),
            )
        return _conservative_recommendation(
            environment=env,
            provider="ollama",
            model="qwen2.5-coder:7b",
            context_window_tokens=32000,
            max_input_tokens=8000,
            max_output_tokens=1024,
            rag_budget_tokens=12000,
            patch_size_lines=120,
            local_execution_weight=1.0,
            remote_execution_weight=0.0,
            requires_explicit_provider_config=True,
            notes=(
                "Remote mode requested without explicit provider config; falling back to local-safe defaults.",
                "Cloud/provider billing is never enabled silently.",
            ),
        )

    if env == "mixed-local-remote":
        if has_explicit_remote:
            return _conservative_recommendation(
                environment=env,
                provider="openai-compatible",
                model="model",
                context_window_tokens=64000,
                max_input_tokens=20000,
                max_output_tokens=2048,
                rag_budget_tokens=32000,
                patch_size_lines=180,
                local_execution_weight=0.7,
                remote_execution_weight=0.3,
                requires_explicit_provider_config=False,
                notes=(
                    "Mixed mode prefers local execution and uses remote as bounded overflow path.",
                    "Remote usage depends on explicit endpoint/API-key configuration.",
                ),
            )
        return _conservative_recommendation(
            environment=env,
            provider="ollama",
            model="qwen2.5-coder:14b",
            context_window_tokens=64000,
            max_input_tokens=16000,
            max_output_tokens=2048,
            rag_budget_tokens=32000,
            patch_size_lines=180,
            local_execution_weight=1.0,
            remote_execution_weight=0.0,
            requires_explicit_provider_config=True,
            notes=(
                "Mixed mode requested without explicit remote endpoint; remote path stays disabled by default.",
                "Cloud/provider billing is never enabled silently.",
            ),
        )

    raise ValueError(f"unsupported environment: {env}")

