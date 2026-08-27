"""Hub-owned policy and read models for the local KAT/LFM/Needle runtime.

This module deliberately does not start processes or execute tools.  It gives
the Hub deterministic placement and model-routing decisions; process control
stays in the operator adapter and delegated execution stays with Workers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence
from urllib.parse import urlsplit

MiB = 1024 * 1024
GiB = 1024 * MiB

RuntimeId = Literal["kat", "lfm", "needle"]
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


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
        if self.runtime_id not in {"kat", "lfm", "needle"}:
            raise ValueError("local_model_runtime_id_invalid")
        if _IDENTIFIER.fullmatch(str(self.provider_id)) is None or _IDENTIFIER.fullmatch(str(self.model_id)) is None:
            raise ValueError("local_model_identifier_invalid")
        if self.execution_device not in {"cuda", "cpu"}:
            raise ValueError("local_model_execution_device_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.context_capacity, self.default_context, self.vram_budget_bytes, self.ram_budget_bytes)
        ):
            raise ValueError("local_model_resource_budget_invalid")
        if self.default_context <= 0 or self.default_context > self.context_capacity:
            raise ValueError("local_model_context_budget_invalid")
        if self.execution_device == "cpu" and self.vram_budget_bytes:
            raise ValueError("cpu_runtime_must_not_reserve_vram")
        if self.endpoint is not None:
            parsed = urlsplit(self.endpoint)
            if (
                parsed.scheme != "http"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.hostname not in {"127.0.0.1", "::1", "localhost", "host.docker.internal"}
                or parsed.port is None
            ):
                raise ValueError("local_model_endpoint_must_be_explicit_internal_http")


@dataclass(frozen=True)
class RuntimeResourceMeasurement:
    vram_used_bytes: int = 0
    ram_used_bytes: int = 0

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.vram_used_bytes, self.ram_used_bytes)
        ):
            raise ValueError("local_runtime_resource_measurement_invalid")


@dataclass(frozen=True)
class ResourceSnapshot:
    total_vram_bytes: int
    free_vram_bytes: int
    available_ram_bytes: int
    runtime_usage: Mapping[RuntimeId, RuntimeResourceMeasurement] = field(default_factory=dict)
    active_contexts: Mapping[RuntimeId, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.total_vram_bytes, self.free_vram_bytes, self.available_ram_bytes)
        ):
            raise ValueError("local_runtime_resource_snapshot_invalid")
        if self.free_vram_bytes > self.total_vram_bytes:
            raise ValueError("local_runtime_resource_snapshot_invalid")
        if any(runtime_id not in {"kat", "lfm", "needle"} for runtime_id in self.runtime_usage):
            raise ValueError("local_runtime_resource_measurement_runtime_invalid")
        if self.active_contexts and set(self.active_contexts) != {"kat", "lfm", "needle"}:
            raise ValueError("local_runtime_active_context_set_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100_000_000
            for value in self.active_contexts.values()
        ):
            raise ValueError("local_runtime_active_context_invalid")


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
        if isinstance(reserve_vram_bytes, bool) or not isinstance(reserve_vram_bytes, int) or reserve_vram_bytes < 0:
            raise ValueError("local_model_vram_reserve_invalid")
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
        reclaimable_vram = sum(
            min(
                resources.runtime_usage.get(model.runtime_id, RuntimeResourceMeasurement()).vram_used_bytes,
                model.vram_budget_bytes,
            )
            for model in models
        )
        available_for_models = max(
            0,
            min(resources.total_vram_bytes, resources.free_vram_bytes + reclaimable_vram) - self._reserve_vram_bytes,
        )
        if required > available_for_models:
            lfm = by_id["lfm"]
            if lfm.default_context > 16384:
                contexts["lfm"] = 16384
                # Conservative measured-at-runtime estimate: reclaim only a
                # bounded 512 MiB; startup still fails closed if insufficient.
                required = max(0, required - 512 * MiB)
        if required > available_for_models:
            return PlacementDecision(
                False,
                "insufficient_vram_with_reserve",
                (),
                contexts,
                required,
                self._reserve_vram_bytes,
            )
        required_ram = sum(model.ram_budget_bytes for model in models)
        reclaimable_ram = sum(
            min(
                resources.runtime_usage.get(model.runtime_id, RuntimeResourceMeasurement()).ram_used_bytes,
                model.ram_budget_bytes,
            )
            for model in models
        )
        if required_ram > resources.available_ram_bytes + reclaimable_ram:
            return PlacementDecision(
                False,
                "insufficient_system_ram",
                (),
                contexts,
                required,
                self._reserve_vram_bytes,
            )
        return PlacementDecision(
            True,
            "placement_admitted",
            ("lfm", "kat", "needle"),
            contexts,
            required,
            self._reserve_vram_bytes,
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

    def __post_init__(self) -> None:
        if not str(self.task_kind).strip():
            raise ValueError("local_model_route_task_kind_invalid")
        if isinstance(self.prompt_chars, bool) or not isinstance(self.prompt_chars, int) or self.prompt_chars < 0:
            raise ValueError("local_model_route_prompt_size_invalid")
        if self.needle_confidence is not None and (
            isinstance(self.needle_confidence, bool)
            or not isinstance(self.needle_confidence, (int, float))
            or not math.isfinite(float(self.needle_confidence))
            or not 0.0 <= float(self.needle_confidence) <= 1.0
        ):
            raise ValueError("local_model_route_confidence_invalid")


@dataclass(frozen=True)
class ModelRouteDecision:
    target: Literal["needle_tool", "lfm", "kat"]
    reason_code: str
    fallback_chain: tuple[str, ...]


class LocalModelRoutingPolicy:
    """Hub policy: Needle proposes; only the Hub chooses an execution path."""

    _COMPLEX_KINDS = frozenset(
        {
            "architecture",
            "bugfix",
            "coding",
            "debugging",
            "planning",
            "refactor",
            "repo_analysis",
            "research",
        }
    )
    _LIGHT_KINDS = frozenset(
        {
            "classification",
            "preprocess",
            "simple_code",
            "summarization",
            "short_answer",
        }
    )

    def __init__(self, *, needle_threshold: float = 0.9, light_prompt_chars: int = 6000) -> None:
        if (
            isinstance(needle_threshold, bool)
            or not isinstance(needle_threshold, (int, float))
            or not math.isfinite(float(needle_threshold))
            or not 0.0 <= float(needle_threshold) <= 1.0
        ):
            raise ValueError("local_model_route_threshold_invalid")
        if isinstance(light_prompt_chars, bool) or not isinstance(light_prompt_chars, int) or light_prompt_chars < 1:
            raise ValueError("local_model_route_prompt_limit_invalid")
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
            "kat",
            "openai_compatible",
            "kat-coder-v2.5-dev",
            "http://127.0.0.1:8082/v1",
            "cuda",
            65536,
            32768,
            frozenset({"chat", "coding", "debugging", "planning", "reasoning"}),
            vram_budget_bytes=4 * GiB,
            ram_budget_bytes=54 * GiB,
            metadata={"cuda_expert_gb": 4, "device_selector": "COLI_GPUS=0"},
        ),
        LocalModelCapability(
            "lfm",
            "llamacpp",
            "lfm2.5-2.6b-agentic-q8_0",
            "http://127.0.0.1:8081/v1",
            "cuda",
            131072,
            32768,
            frozenset({"chat", "classification", "code_simple", "json", "summarization"}),
            vram_budget_bytes=3 * GiB,
            ram_budget_bytes=1 * GiB,
            metadata={"gpu_offload": "all", "context_pressure_fallback": 16384},
        ),
        LocalModelCapability(
            "needle",
            "needle_sidecar",
            "needle-2-45m",
            "http://127.0.0.1:8083",
            "cpu",
            256,
            256,
            frozenset({"argument_extraction", "intent", "json", "tool_selection"}),
            ram_budget_bytes=256 * MiB,
            metadata={"orchestration_authority": False, "training_threads_max": 4},
        ),
    )
