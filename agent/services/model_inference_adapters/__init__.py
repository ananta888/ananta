"""RTIPM-004: Model inference adapter base and capability declarations.

Each adapter declares its ``CAPABILITIES`` set. The
``RestrictedModelInferenceService`` checks capabilities before dispatching
an operation to ensure only supported ops reach each adapter.

Adapter contract
────────────────
- ``embed(texts)`` → list[list[float]]
- ``classify(text, labels)`` → ClassificationResult
- ``rerank(query, candidates)`` → list[RerankResult]
- ``score_choices(prompt, choices)`` → list[ChoiceScore]
- ``extract_features(text)`` → FeatureVector
- ``risk_score(input_dict)`` → RiskScoreResult

All operations must be pure analysis — no free text generation is returned.
Missing optional dependencies produce ``degraded`` status, not a crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping

# ── Capability constants ──────────────────────────────────────────────────────
CAP_EMBEDDINGS = "embeddings"
CAP_HIDDEN_STATES = "hidden_states"
CAP_ATTENTION = "attention"
CAP_LOGITS = "logits"
CAP_CLASSIFICATION = "classification"
CAP_RERANK = "rerank"
CAP_FEATURE_EXTRACTION = "feature_extraction"
CAP_CHOICE_SCORING = "choice_scoring"

ALL_CAPABILITIES = frozenset(
    {
        CAP_EMBEDDINGS,
        CAP_HIDDEN_STATES,
        CAP_ATTENTION,
        CAP_LOGITS,
        CAP_CLASSIFICATION,
        CAP_RERANK,
        CAP_FEATURE_EXTRACTION,
        CAP_CHOICE_SCORING,
    }
)

SUPPORT_SUPPORTED = "supported"
SUPPORT_UNSUPPORTED = "unsupported"
SUPPORT_CONDITIONAL = "conditional"
CAPABILITY_SUPPORT_STATES = frozenset(
    {
        SUPPORT_SUPPORTED,
        SUPPORT_UNSUPPORTED,
        SUPPORT_CONDITIONAL,
    }
)

# A capability is externally probeable only when it maps to a public adapter
# operation. Hidden states, attention and raw logits intentionally remain
# unadvertised until a dedicated inspection port exposes them.
CAPABILITY_OPERATION_METHODS: Mapping[str, str] = {
    CAP_EMBEDDINGS: "embed",
    CAP_CLASSIFICATION: "classify",
    CAP_RERANK: "rerank",
    CAP_FEATURE_EXTRACTION: "extract_features",
    CAP_CHOICE_SCORING: "score_choices",
}


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Machine-readable support statement for one adapter capability."""

    name: str
    support: str
    reason_code: str
    operation: str = ""

    def __post_init__(self) -> None:
        if self.name not in ALL_CAPABILITIES:
            raise ValueError(f"unknown_capability:{self.name}")
        if self.support not in CAPABILITY_SUPPORT_STATES:
            raise ValueError(f"invalid_capability_support:{self.support}")
        if not self.reason_code:
            raise ValueError("capability_reason_code_required")

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "operation": self.operation,
            "reason_code": self.reason_code,
            "support": self.support,
        }


@dataclass(frozen=True)
class AdapterCapabilityDescriptor:
    """Adapter-owned, deterministic capability declaration."""

    engine: str
    adapter_class: str
    capabilities: tuple[CapabilityDescriptor, ...]

    def __post_init__(self) -> None:
        names = tuple(item.name for item in self.capabilities)
        if names != tuple(sorted(ALL_CAPABILITIES)):
            raise ValueError("descriptor_must_cover_all_capabilities_once")

    def advertised_capabilities(self) -> frozenset[str]:
        return frozenset(
            item.name for item in self.capabilities if item.support != SUPPORT_UNSUPPORTED and item.operation
        )

    def capability(self, name: str) -> CapabilityDescriptor:
        for item in self.capabilities:
            if item.name == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_class": self.adapter_class,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "engine": self.engine,
        }

# ── Result types (shared across all adapters) ─────────────────────────────────


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    all_scores: dict[str, float] = field(default_factory=dict)
    model_id: str = ""
    engine: str = ""
    latency_ms: float = 0.0
    no_generation: bool = True


@dataclass
class RerankResult:
    path: str
    record_id: str
    score: float  # 0.0 – 1.0
    reason_code: str = ""
    model_id: str = ""
    engine: str = ""
    manifest_digest: str = ""
    confidence: float = 1.0
    no_generation: bool = True


@dataclass
class ChoiceScore:
    choice: str
    score: float  # higher = more likely under the model
    model_id: str = ""
    engine: str = ""
    no_generation: bool = True


@dataclass
class FeatureVector:
    vector: list[float] = field(default_factory=list)
    dimensions: int = 0
    model_id: str = ""
    engine: str = ""
    no_generation: bool = True


@dataclass
class RiskScoreResult:
    risk_score: float  # 0.0 – 1.0
    risk_category: str = ""  # fixed enum: low / medium / high / critical
    confidence: float = 1.0
    model_id: str = ""
    engine: str = ""
    no_generation: bool = True


# ── Adapter status ────────────────────────────────────────────────────────────


@dataclass
class AdapterStatus:
    name: str
    engine: str
    status: str  # ready / degraded / unavailable
    capabilities: frozenset[str] = field(default_factory=frozenset)
    model_id: str = ""
    device: str = ""
    revision: str = ""
    error: str = ""

    def has_capability(self, cap: str) -> bool:
        return cap in self.capabilities


# ── Abstract base ─────────────────────────────────────────────────────────────


class BaseInferenceAdapter:
    """Abstract base for all restricted model inference adapters.

    Subclasses **must** set ``CAPABILITIES`` and implement the operations
    they advertise. Unsupported operations raise ``NotImplementedError``.
    """

    ENGINE: str = "base"
    CAPABILITIES: frozenset[str] = frozenset()
    CAPABILITY_DECLARATIONS: ClassVar[Mapping[str, tuple[str, str]]] = {}

    @classmethod
    def capability_descriptor(cls) -> AdapterCapabilityDescriptor:
        """Describe support without importing optional runtimes or loading weights."""

        capabilities: list[CapabilityDescriptor] = []
        for capability in sorted(ALL_CAPABILITIES):
            declaration = cls.CAPABILITY_DECLARATIONS.get(capability)
            if declaration is None:
                support = SUPPORT_SUPPORTED if capability in cls.CAPABILITIES else SUPPORT_UNSUPPORTED
                reason_code = "operation_implemented" if capability in cls.CAPABILITIES else "operation_not_implemented"
            else:
                support, reason_code = declaration
            capabilities.append(
                CapabilityDescriptor(
                    name=capability,
                    support=support,
                    reason_code=reason_code,
                    operation=CAPABILITY_OPERATION_METHODS.get(capability, ""),
                )
            )
        return AdapterCapabilityDescriptor(
            engine=cls.ENGINE,
            adapter_class=f"{cls.__module__}.{cls.__qualname__}",
            capabilities=tuple(capabilities),
        )

    def status(self) -> AdapterStatus:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        raise NotImplementedError

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        raise NotImplementedError

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        raise NotImplementedError

    def extract_features(self, text: str) -> FeatureVector:
        raise NotImplementedError

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        raise NotImplementedError
