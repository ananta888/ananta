from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from .resources import VoiceResourceBudget

if TYPE_CHECKING:
    from .config import VoiceRuntimeConfig


HUB_CONFIGURATION_FIELDS = frozenset(
    {
        "transport_mode",
        "recognition_strategy",
        "routing_strategy",
        "correction_policy",
        "review_policy",
        "primary_backend",
        "secondary_backends",
        "max_parallel_backends",
        "candidate_deadline_sec",
        "confidence_threshold",
        "resource_max_ram_mb",
        "resource_max_vram_mb",
        "resource_max_concurrent_backends",
        "resource_max_audio_seconds",
        "resource_max_queue_depth",
        "enhancement_variants",
        "diarization_backend",
        "feature_flags",
    }
)
HUB_FEATURE_FLAGS = frozenset(
    {
        "voice_fusion",
        "audio_enhancement",
        "adaptive_routing",
        "restricted_worker",
        "codecompass_reranking",
        "personalization",
        "optional_models",
        "generative_judge",
    }
)
HUB_BACKENDS = frozenset({"vosk", "whisper_cpp", "faster_whisper", "voxtral"})
HUB_TRANSPORT_MODES = frozenset({"batch", "streaming"})
HUB_RECOGNITION_STRATEGIES = frozenset({"single", "parallel_compare", "classic_then_correct"})
HUB_ROUTING_STRATEGIES = frozenset({"fixed", "fallback", "adaptive"})
HUB_CORRECTION_POLICIES = frozenset({"none", "deterministic", "restricted_choice", "generative_local"})
HUB_REVIEW_POLICIES = frozenset({"automatic", "on_disagreement", "always"})
HUB_ENHANCEMENT_VARIANTS = frozenset({"original", "bypass", "normalized", "high_pass", "speech_safe"})
HUB_DIARIZATION_BACKENDS = frozenset({"none", "pyannote"})


@dataclass(frozen=True)
class HubVoiceConfiguration:
    transport_mode: str = "batch"
    recognition_strategy: str = "single"
    routing_strategy: str = "fallback"
    correction_policy: str = "deterministic"
    review_policy: str = "on_disagreement"
    primary_backend: str = "vosk"
    secondary_backends: tuple[str, ...] = ("whisper_cpp",)
    max_parallel_backends: int = 2
    candidate_deadline_sec: float = 120.0
    confidence_threshold: float = 0.7
    resource_max_ram_mb: int | None = None
    resource_max_vram_mb: int | None = None
    resource_max_concurrent_backends: int | None = None
    resource_max_audio_seconds: int | None = None
    resource_max_queue_depth: int | None = None
    enhancement_variants: tuple[str, ...] | None = None
    diarization_backend: str | None = None
    feature_flags: Mapping[str, bool] = field(default_factory=lambda: {name: False for name in HUB_FEATURE_FLAGS})

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "HubVoiceConfiguration":
        if not isinstance(raw, Mapping):
            raise ValueError("voice execution configuration must be an object")
        unknown = set(raw) - HUB_CONFIGURATION_FIELDS
        if unknown:
            raise ValueError("voice execution configuration contains unknown or administrative fields")
        transport = _enum(raw, "transport_mode", "batch", HUB_TRANSPORT_MODES)
        recognition = _enum(raw, "recognition_strategy", "single", HUB_RECOGNITION_STRATEGIES)
        routing = _enum(raw, "routing_strategy", "fallback", HUB_ROUTING_STRATEGIES)
        correction = _enum(raw, "correction_policy", "deterministic", HUB_CORRECTION_POLICIES)
        review = _enum(raw, "review_policy", "on_disagreement", HUB_REVIEW_POLICIES)
        primary = _enum(raw, "primary_backend", "vosk", HUB_BACKENDS)
        secondary = _backends(raw.get("secondary_backends", ["whisper_cpp"]))
        if primary in secondary:
            raise ValueError("voice execution primary backend cannot be duplicated")
        flags = _feature_flags(raw.get("feature_flags", {}))
        return cls(
            transport_mode=transport,
            recognition_strategy=recognition,
            routing_strategy=routing,
            correction_policy=correction,
            review_policy=review,
            primary_backend=primary,
            secondary_backends=secondary,
            max_parallel_backends=_bounded_int(raw, "max_parallel_backends", 2, minimum=1, maximum=4),
            candidate_deadline_sec=_bounded_float(
                raw,
                "candidate_deadline_sec",
                120.0,
                minimum=0.001,
                maximum=300.0,
            ),
            confidence_threshold=_bounded_float(
                raw,
                "confidence_threshold",
                0.7,
                minimum=0.0,
                maximum=1.0,
            ),
            resource_max_ram_mb=_optional_bounded_int(
                raw,
                "resource_max_ram_mb",
                minimum=1,
                maximum=1_048_576,
            ),
            resource_max_vram_mb=_optional_bounded_int(
                raw,
                "resource_max_vram_mb",
                minimum=0,
                maximum=1_048_576,
            ),
            resource_max_concurrent_backends=_optional_bounded_int(
                raw,
                "resource_max_concurrent_backends",
                minimum=1,
                maximum=32,
            ),
            resource_max_audio_seconds=_optional_bounded_int(
                raw,
                "resource_max_audio_seconds",
                minimum=1,
                maximum=86_400,
            ),
            resource_max_queue_depth=_optional_bounded_int(
                raw,
                "resource_max_queue_depth",
                minimum=1,
                maximum=128,
            ),
            enhancement_variants=_enhancement_variants(raw["enhancement_variants"])
            if "enhancement_variants" in raw
            else None,
            diarization_backend=_enum(raw, "diarization_backend", "none", HUB_DIARIZATION_BACKENDS)
            if "diarization_backend" in raw
            else None,
            feature_flags=flags,
        )


@dataclass(frozen=True)
class VoiceExecutionPolicy:
    transport_mode: str
    recognition_strategy: str
    routing_strategy: str
    correction_policy: str
    review_policy: str
    primary_backend: str
    secondary_backends: tuple[str, ...]
    max_parallel_backends: int
    max_candidate_count: int
    candidate_deadline_sec: float
    confidence_threshold: float
    enhancement_variants: tuple[str, ...]
    diarization_backend: str
    feature_flags: Mapping[str, bool]
    resource_budget: VoiceResourceBudget
    source: str
    adjustments: tuple[Mapping[str, str], ...] = ()

    @classmethod
    def resolve(
        cls,
        runtime: "VoiceRuntimeConfig",
        hub: HubVoiceConfiguration | None,
    ) -> "VoiceExecutionPolicy":
        runtime_budget = _runtime_resource_budget(runtime)
        if hub is None:
            static_flags = {
                "voice_fusion": runtime.recognition_strategy in {"parallel_compare", "parallel_fusion"},
                "audio_enhancement": runtime.audio_enhancement_enabled,
                "adaptive_routing": runtime.adaptive_routing_enabled,
                "restricted_worker": runtime.correction_policy == "restricted_choice",
                "codecompass_reranking": False,
                "personalization": True,
                "optional_models": runtime.diarization_backend == "pyannote",
                "generative_judge": runtime.correction_policy == "local_schema_corrector",
            }
            return cls(
                transport_mode=runtime.transport_mode,
                recognition_strategy=runtime.recognition_strategy,
                routing_strategy=runtime.routing_strategy,
                correction_policy=runtime.correction_policy,
                review_policy=runtime.review_policy,
                primary_backend=runtime.primary_backend,
                secondary_backends=runtime.secondary_backends,
                max_parallel_backends=runtime.max_parallel_backends,
                max_candidate_count=runtime.max_candidate_count,
                candidate_deadline_sec=runtime.candidate_deadline_sec,
                confidence_threshold=runtime.confidence_threshold,
                enhancement_variants=runtime.enhancement_variants,
                diarization_backend=runtime.diarization_backend,
                feature_flags=static_flags,
                resource_budget=runtime_budget,
                source="runtime_default",
            )

        _require_allowed(hub.primary_backend, runtime.policy_allowed_backends, "primary backend")
        for backend in hub.secondary_backends:
            _require_allowed(backend, runtime.policy_allowed_backends, "secondary backend")
        _require_allowed(
            hub.recognition_strategy,
            runtime.policy_allowed_recognition_strategies,
            "recognition strategy",
        )
        normalized_routing = {"fixed": "fixed", "fallback": "fixed", "adaptive": "adaptive_local"}[hub.routing_strategy]
        _require_allowed(normalized_routing, runtime.policy_allowed_routing_strategies, "routing strategy")
        normalized_correction = {
            "none": "none",
            "deterministic": "rules",
            "restricted_choice": "restricted_choice",
            "generative_local": "local_schema_corrector",
        }[hub.correction_policy]
        _require_allowed(normalized_correction, runtime.policy_allowed_correction_policies, "correction policy")
        _require_allowed(hub.review_policy, runtime.policy_allowed_review_policies, "review policy")

        runtime_feature_limits = {
            "voice_fusion": runtime.voice_fusion_enabled,
            "audio_enhancement": runtime.audio_enhancement_enabled,
            "adaptive_routing": runtime.adaptive_routing_enabled,
            "restricted_worker": runtime.restricted_choice_hook_enabled,
            "codecompass_reranking": runtime.codecompass_reranking_enabled,
            "personalization": runtime.personalization_enabled,
            "optional_models": runtime.optional_models_enabled,
            "generative_judge": runtime.generative_judge_hook_enabled,
        }
        effective_flags = {
            name: bool(hub.feature_flags.get(name, False) and runtime_feature_limits[name])
            for name in sorted(HUB_FEATURE_FLAGS)
        }
        adjustments: list[Mapping[str, str]] = []
        recognition = hub.recognition_strategy
        if recognition == "parallel_compare" and not effective_flags["voice_fusion"]:
            adjustments.append(_adjustment("recognition_strategy", recognition, "single", "voice_fusion_disabled"))
            recognition = "single"
        routing = normalized_routing
        if routing == "adaptive_local" and not effective_flags["adaptive_routing"]:
            adjustments.append(_adjustment("routing_strategy", routing, "fixed", "adaptive_routing_disabled"))
            routing = "fixed"
        correction = normalized_correction
        if correction == "restricted_choice" and not effective_flags["restricted_worker"]:
            adjustments.append(_adjustment("correction_policy", correction, "rules", "restricted_worker_disabled"))
            correction = "rules"
        elif correction == "local_schema_corrector" and not effective_flags["generative_judge"]:
            adjustments.append(_adjustment("correction_policy", correction, "rules", "generative_judge_disabled"))
            correction = "rules"
        requested_variants = hub.enhancement_variants or runtime.enhancement_variants
        allowed_variants = set(runtime.enhancement_variants)
        enhancement_variants = tuple(item for item in requested_variants if item in allowed_variants)
        if not enhancement_variants or enhancement_variants[0] != "original":
            enhancement_variants = ("original",)
        if not (effective_flags["audio_enhancement"] and effective_flags["voice_fusion"]):
            if requested_variants != ("original",):
                adjustments.append(
                    _adjustment(
                        "enhancement_variants",
                        ",".join(requested_variants),
                        "original",
                        "audio_enhancement_disabled",
                    )
                )
            enhancement_variants = ("original",)
        diarization_backend = hub.diarization_backend or runtime.diarization_backend
        if diarization_backend == "pyannote" and runtime.diarization_backend != "pyannote":
            adjustments.append(_adjustment("diarization_backend", "pyannote", "none", "runtime_unavailable"))
            diarization_backend = "none"
        if diarization_backend == "pyannote" and not effective_flags["optional_models"]:
            adjustments.append(_adjustment("diarization_backend", "pyannote", "none", "optional_models_disabled"))
            diarization_backend = "none"
        requested_budget = VoiceResourceBudget(
            max_ram_bytes=(hub.resource_max_ram_mb or runtime.resource_max_ram_mb)
            * 1024
            * 1024,
            max_vram_bytes=(
                hub.resource_max_vram_mb
                if hub.resource_max_vram_mb is not None
                else runtime.resource_max_vram_mb
            )
            * 1024
            * 1024,
            max_concurrent_backends=hub.resource_max_concurrent_backends
            or runtime.resource_max_concurrent_backends,
            max_audio_ms=(
                hub.resource_max_audio_seconds or runtime.resource_max_audio_seconds
            )
            * 1000,
            max_queue_depth=hub.resource_max_queue_depth
            or runtime.resource_max_queue_depth,
        )
        effective_budget = runtime_budget.narrowed_by(requested_budget)
        effective_parallel = min(
            hub.max_parallel_backends,
            runtime.max_parallel_backends,
            effective_budget.max_concurrent_backends,
        )
        return cls(
            transport_mode="stream" if hub.transport_mode == "streaming" else "batch",
            recognition_strategy=recognition,
            routing_strategy=routing,
            correction_policy=correction,
            review_policy=hub.review_policy,
            primary_backend=hub.primary_backend,
            secondary_backends=hub.secondary_backends,
            max_parallel_backends=effective_parallel,
            max_candidate_count=min(
                runtime.max_candidate_count,
                effective_parallel * len(enhancement_variants),
                effective_budget.max_queue_depth,
            ),
            candidate_deadline_sec=min(hub.candidate_deadline_sec, runtime.candidate_deadline_sec),
            confidence_threshold=hub.confidence_threshold,
            enhancement_variants=enhancement_variants,
            diarization_backend=diarization_backend,
            feature_flags=effective_flags,
            resource_budget=effective_budget,
            source="hub_context",
            adjustments=tuple(adjustments),
        )


def _enum(raw: Mapping[str, Any], key: str, default: str, allowed: frozenset[str]) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"voice execution {key} is invalid")
    return value


def _backends(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list) or len(raw) > 3 or any(not isinstance(item, str) for item in raw):
        raise ValueError("voice execution secondary_backends is invalid")
    values = tuple(raw)
    if len(set(values)) != len(values) or any(value not in HUB_BACKENDS for value in values):
        raise ValueError("voice execution secondary_backends is invalid")
    return values


def _feature_flags(raw: object) -> dict[str, bool]:
    if not isinstance(raw, Mapping) or set(raw) - HUB_FEATURE_FLAGS:
        raise ValueError("voice execution feature_flags contains unknown fields")
    if any(not isinstance(value, bool) for value in raw.values()):
        raise ValueError("voice execution feature flags must be boolean")
    compatibility_defaults = {
        "audio_enhancement": bool(raw.get("voice_fusion", False)),
        "adaptive_routing": bool(raw.get("optional_models", False)),
    }
    return {
        name: bool(raw.get(name, compatibility_defaults.get(name, False)))
        for name in sorted(HUB_FEATURE_FLAGS)
    }


def _enhancement_variants(raw: object) -> tuple[str, ...]:
    if (
        not isinstance(raw, list)
        or not 1 <= len(raw) <= 4
        or any(not isinstance(item, str) or item not in HUB_ENHANCEMENT_VARIANTS for item in raw)
    ):
        raise ValueError("voice execution enhancement_variants is invalid")
    values = tuple(raw)
    if len(set(values)) != len(values) or values[0] != "original":
        raise ValueError("voice execution enhancement_variants is invalid")
    return values


def _bounded_int(
    raw: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"voice execution {key} is invalid")
    return value


def _bounded_float(
    raw: Mapping[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"voice execution {key} is invalid")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"voice execution {key} is invalid")
    return normalized


def _optional_bounded_int(
    raw: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if key not in raw:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"voice execution {key} is invalid")
    return value


def _runtime_resource_budget(runtime: "VoiceRuntimeConfig") -> VoiceResourceBudget:
    return VoiceResourceBudget(
        max_ram_bytes=runtime.resource_max_ram_mb * 1024 * 1024,
        max_vram_bytes=runtime.resource_max_vram_mb * 1024 * 1024,
        max_concurrent_backends=min(
            runtime.resource_max_concurrent_backends,
            runtime.max_queue_depth,
        ),
        max_audio_ms=min(
            runtime.resource_max_audio_seconds,
            runtime.max_audio_duration_sec,
        )
        * 1000,
        max_queue_depth=min(
            runtime.resource_max_queue_depth,
            runtime.max_queue_depth,
        ),
    )


def _require_allowed(value: str, allowed: tuple[str, ...], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"Hub-selected voice {label} exceeds the runtime policy envelope")


def _adjustment(field: str, requested: str, effective: str, reason: str) -> Mapping[str, str]:
    return {"field": field, "requested": requested, "effective": effective, "reason_code": reason}
