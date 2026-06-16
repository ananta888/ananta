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
from typing import Any

# ── Capability constants ──────────────────────────────────────────────────────
CAP_EMBEDDINGS = "embeddings"
CAP_HIDDEN_STATES = "hidden_states"
CAP_ATTENTION = "attention"
CAP_LOGITS = "logits"
CAP_CLASSIFICATION = "classification"
CAP_RERANK = "rerank"
CAP_FEATURE_EXTRACTION = "feature_extraction"
CAP_CHOICE_SCORING = "choice_scoring"

ALL_CAPABILITIES = frozenset({
    CAP_EMBEDDINGS, CAP_HIDDEN_STATES, CAP_ATTENTION, CAP_LOGITS,
    CAP_CLASSIFICATION, CAP_RERANK, CAP_FEATURE_EXTRACTION, CAP_CHOICE_SCORING,
})

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
    score: float          # 0.0 – 1.0
    reason_code: str = ""
    model_id: str = ""
    engine: str = ""
    confidence: float = 1.0
    no_generation: bool = True


@dataclass
class ChoiceScore:
    choice: str
    score: float          # higher = more likely under the model
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
    risk_score: float         # 0.0 – 1.0
    risk_category: str = ""   # fixed enum: low / medium / high / critical
    confidence: float = 1.0
    model_id: str = ""
    engine: str = ""
    no_generation: bool = True


# ── Adapter status ────────────────────────────────────────────────────────────

@dataclass
class AdapterStatus:
    name: str
    engine: str
    status: str            # ready / degraded / unavailable
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
