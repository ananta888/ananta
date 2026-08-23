"""Hub-owned policy and read models for the local KAT/LFM/Needle runtime.

This module deliberately does not start processes or execute tools.  It gives
the Hub deterministic placement and model-routing decisions; process control
stays in the operator adapter and delegated execution stays with Workers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

MiB = 1024 * 1024
GiB = 1024 * MiB

RuntimeId = Literal["kat", "lfm", "needle"]


@dataclass(frozen=True)
class LocalModelCapability:
    runtime_id: RuntimeId
    provider_id: str
    model_id: str
    endpoint: str | None
    execution_device: Literal["cuda", "cpu"]
    context_capacity: int
    default_context: int
    capabilities: frozenset[str]
    vram_budget_bytes: int = 0
    ram_budget_bytes: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default_context <= 0 or self.default_context > self.context_capacity:
            raise ValueError("local_model_context_budget_invalid")
        if self.execution_device == "cpu" and self.vram_budget_bytes:
            raise ValueError("cpu_runtime_must_not_reserve_vram")
        if self.endpoint is not None and not self.endpoint.startswith("http://127.0.0.1:"):
            raise ValueError("local_model_endpoint_must_be_loopback")


@dataclass(frozen=True)
class ResourceSnapshot:
    total_vram_bytes: int
    free_vram_bytes: int
    available_ram_bytes: int


@dataclass(frozen=True)
class PlacementDecision:
    admitted: bool
    reason_code: str
    start_order: tuple[RuntimeId, ...]
    effective_contexts: Mapping[RuntimeId, int]
    required_vram_bytes: int
    reserve_vram_bytes: int


class LocalModelPlacementPolicy:
    """Deterministic RTX-3080 placement policy; no process side effects."""

    def __init__(self, *, reserve_vram_bytes: int = 1536 * MiB) -> None:
        self._reserve_vram_bytes = reserve_vram_bytes

    def decide(
        self,
        models: Sequence[LocalModelCapability],
        resources: ResourceSnapshot,
    ) -> PlacementDecision:
        by_id = {model.runtime_id: model for model in models}
        if set(by_id) != {"kat", "lfm", "needle"}:
            return PlacementDecision(False, "runtime_set_incomplete", (), {}, 0, self._reserve_vram_bytes)
        required = sum(model.vram_budget_bytes for model in models)
        contexts = {model.runtime_id: model.default_context for model in models}
        available_for_models = max(0, resources.free_vram_bytes - self._reserve_vram_bytes)
        if required > available_for_models:
            lfm = by_id["lfm"]
            if lfm.default_context > 16384:
                contexts["lfm"] = 16384
                # Conservative measured-at-runtime estimate: reclaim only a
                # bounded 512 MiB; startup still fails closed if insufficient.
                required = max(0, required - 512 * MiB)
        if required > available_for_models:
            return PlacementDecision(
                False, "insufficient_vram_with_reserve", (), contexts,
                required, self._reserve_vram_bytes,
            )
        required_ram = sum(model.ram_budget_bytes for model in models)
        if required_ram > resources.available_ram_bytes:
            return PlacementDecision(
                False, "insufficient_system_ram", (), contexts,
                required, self._reserve_vram_bytes,
            )
        return PlacementDecision(
            True, "placement_admitted", ("lfm", "kat", "needle"), contexts,
            required, self._reserve_vram_bytes,
        )


@dataclass(frozen=True)
class ModelRouteRequest:
    task_kind: str
    prompt_chars: int
    requires_tools: bool = False
    requires_json: bool = False
    needle_candidate_valid: bool = False
    needle_confidence: float | None = None
    tool_risk_class: str | None = None


@dataclass(frozen=True)
class ModelRouteDecision:
    target: Literal["needle_tool", "lfm", "kat"]
    reason_code: str
    fallback_chain: tuple[str, ...]


class LocalModelRoutingPolicy:
    """Hub policy: Needle proposes; only the Hub chooses an execution path."""

    _COMPLEX_KINDS = frozenset({
        "architecture", "bugfix", "coding", "debugging", "planning",
        "refactor", "repo_analysis", "research",
    })
    _LIGHT_KINDS = frozenset({
        "classification", "preprocess", "simple_code", "summarization",
        "short_answer",
    })

    def __init__(self, *, needle_threshold: float = 0.9, light_prompt_chars: int = 6000) -> None:
        self._needle_threshold = needle_threshold
        self._light_prompt_chars = light_prompt_chars

    def decide(self, request: ModelRouteRequest) -> ModelRouteDecision:
        confidence = request.needle_confidence
        if (
            request.requires_tools
            and request.needle_candidate_valid
            and confidence is not None
            and confidence >= self._needle_threshold
            and request.tool_risk_class in {"read", "idempotent", "simulated"}
        ):
            return ModelRouteDecision("needle_tool", "validated_high_confidence_safe_tool", ("lfm", "kat"))
        kind = request.task_kind.strip().lower()
        if kind in self._COMPLEX_KINDS:
            return ModelRouteDecision("kat", "complex_task_policy", ())
        if request.requires_tools and (confidence is None or confidence < self._needle_threshold):
            return ModelRouteDecision("kat", "needle_confidence_unusable_or_low", ())
        if kind in self._LIGHT_KINDS and request.prompt_chars <= self._light_prompt_chars:
            return ModelRouteDecision("lfm", "bounded_light_task_policy", ("kat",))
        return ModelRouteDecision("kat", "conservative_default", ())


def rtx3080_local_model_capabilities() -> tuple[LocalModelCapability, ...]:
    """Versioned deployment defaults; artifact hashes live in operator config."""

    return (
        LocalModelCapability(
            "kat", "kat_colibri", "kat-coder-v2.5-dev", "http://127.0.0.1:8082/v1",
            "cuda", 65536, 32768,
            frozenset({"chat", "coding", "debugging", "planning", "reasoning"}),
            vram_budget_bytes=5 * GiB, ram_budget_bytes=40 * GiB,
            metadata={"cuda_expert_gb": 5, "device_selector": "COLI_GPUS=0"},
        ),
        LocalModelCapability(
            "lfm", "lfm_llamacpp", "lfm2.5-2.6b-agentic-q8_0", "http://127.0.0.1:8081/v1",
            "cuda", 131072, 32768,
            frozenset({"chat", "classification", "code_simple", "json", "summarization"}),
            vram_budget_bytes=3 * GiB, ram_budget_bytes=4 * GiB,
            metadata={"gpu_offload": "all", "context_pressure_fallback": 16384},
        ),
        LocalModelCapability(
            "needle", "needle_sidecar", "needle-2-45m", None,
            "cpu", 256, 256,
            frozenset({"argument_extraction", "intent", "json", "tool_selection"}),
            ram_budget_bytes=256 * MiB,
            metadata={"orchestration_authority": False, "training_threads_max": 4},
        ),
    )
