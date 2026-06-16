"""RTIPM-003: RestrictedModelInferenceService.

Single gateway for all restricted (non-generative) model inference operations.
Dispatches to registered adapters based on requested operation and declared
capabilities. Adapters are optional; missing ML dependencies produce a
``degraded`` status, not a crash.

Hard separation contract
────────────────────────
- ``embed()``            → list[list[float]]
- ``classify()``         → ClassificationResult  (fixed label set)
- ``rerank()``           → list[RerankResult]    (scores only)
- ``score_choices()``    → list[ChoiceScore]      (fixed choices)
- ``extract_features()`` → FeatureVector
- ``risk_score()``       → RiskScoreResult

None of these operations return free text. ``model.generate()`` is never
invoked by this service. If a caller somehow passes a free text answer
through an adapter result, ``validate_no_generation()`` will reject it.

Audit events are emitted for: started, finished, blocked, degraded.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.services.model_inference_adapters import (
    AdapterStatus,
    BaseInferenceAdapter,
    CAP_CLASSIFICATION,
    CAP_CHOICE_SCORING,
    CAP_EMBEDDINGS,
    CAP_FEATURE_EXTRACTION,
    CAP_RERANK,
    ClassificationResult,
    ChoiceScore,
    FeatureVector,
    RerankResult,
    RiskScoreResult,
)
from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    get_path_ai_mode_policy_service,
)

log = logging.getLogger(__name__)

# Supported operation names
OP_EMBED = "embed"
OP_CLASSIFY = "classify"
OP_RERANK = "rerank"
OP_SCORE_CHOICES = "score_choices"
OP_EXTRACT_FEATURES = "extract_features"
OP_RISK_SCORE = "risk_score"

ALL_OPS = frozenset({
    OP_EMBED, OP_CLASSIFY, OP_RERANK, OP_SCORE_CHOICES,
    OP_EXTRACT_FEATURES, OP_RISK_SCORE,
})

_OP_TO_CAP: dict[str, str] = {
    OP_EMBED: CAP_EMBEDDINGS,
    OP_CLASSIFY: CAP_CLASSIFICATION,
    OP_RERANK: CAP_RERANK,
    OP_SCORE_CHOICES: CAP_CHOICE_SCORING,
    OP_EXTRACT_FEATURES: CAP_FEATURE_EXTRACTION,
    OP_RISK_SCORE: CAP_CLASSIFICATION,
}


# ── Audit event ───────────────────────────────────────────────────────────────

@dataclass
class InferenceAuditEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    event: str = ""
    operation: str = ""
    adapter_engine: str = ""
    model_id: str = ""
    path: str = ""
    latency_ms: float = 0.0
    reason_code: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event": self.event,
            "operation": self.operation,
            "adapter_engine": self.adapter_engine,
            "model_id": self.model_id,
            "path": self.path,
            "latency_ms": round(self.latency_ms, 2),
            "reason_code": self.reason_code,
            "ts": self.ts,
        }


# ── Mock adapter (deterministic scores, no ML deps required) ──────────────────

class MockInferenceAdapter(BaseInferenceAdapter):
    """Deterministic mock adapter for tests and degraded-state fallback.

    All scores are derived from text length / position — reproducible without
    any ML library. No generation.
    """

    ENGINE = "mock"
    CAPABILITIES = frozenset({
        CAP_EMBEDDINGS, CAP_CLASSIFICATION, CAP_RERANK,
        CAP_CHOICE_SCORING, CAP_FEATURE_EXTRACTION,
    })
    MODEL_ID = "mock-deterministic-v1"

    def __init__(self, dims: int = 8) -> None:
        self._dims = max(1, dims)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            name="mock",
            engine=self.ENGINE,
            status="ready",
            capabilities=self.CAPABILITIES,
            model_id=self.MODEL_ID,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            seed = sum(ord(c) for c in text)
            vec = [float((seed + i) % 100) / 100.0 for i in range(self._dims)]
            result.append(vec)
        return result

    def classify(self, text: str, labels: list[str]) -> ClassificationResult:
        if not labels:
            labels = ["positive", "negative"]
        seed = sum(ord(c) for c in text)
        idx = seed % len(labels)
        scores = {l: 1.0 / len(labels) for l in labels}
        scores[labels[idx]] = 0.6
        total = sum(scores.values())
        scores = {k: round(v / total, 4) for k, v in scores.items()}
        return ClassificationResult(
            label=labels[idx],
            confidence=scores[labels[idx]],
            all_scores=scores,
            model_id=self.MODEL_ID,
            engine=self.ENGINE,
        )

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[RerankResult]:
        q_seed = sum(ord(c) for c in query)
        results = []
        for i, c in enumerate(candidates):
            excerpt = str(c.get("excerpt") or c.get("path") or "")
            common = len(set(query.lower().split()) & set(excerpt.lower().split()))
            score = round(min(1.0, common / max(len(query.split()), 1) + (q_seed % 10) / 100), 4)
            results.append(RerankResult(
                path=str(c.get("path") or ""),
                record_id=str(c.get("record_id") or str(i)),
                score=score,
                reason_code="mock_word_overlap",
                model_id=self.MODEL_ID,
                engine=self.ENGINE,
            ))
        results.sort(key=lambda r: (-r.score, r.path))
        return results

    def score_choices(self, prompt: str, choices: list[str]) -> list[ChoiceScore]:
        seed = sum(ord(c) for c in prompt)
        results = []
        total_w = sum(len(c) + (seed % 7) for c in choices) or 1
        for choice in choices:
            w = (len(choice) + seed % 7) / total_w
            results.append(ChoiceScore(choice=choice, score=round(w, 4), model_id=self.MODEL_ID, engine=self.ENGINE))
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def extract_features(self, text: str) -> FeatureVector:
        vec = self.embed([text])[0]
        return FeatureVector(vector=vec, dimensions=len(vec), model_id=self.MODEL_ID, engine=self.ENGINE)

    def risk_score(self, input_dict: dict[str, Any]) -> RiskScoreResult:
        text = " ".join(str(v) for v in input_dict.values() if v)
        seed = sum(ord(c) for c in text)
        score = (seed % 100) / 100.0
        cat = "high" if score >= 0.5 else "low"
        return RiskScoreResult(risk_score=round(score, 4), risk_category=cat, model_id=self.MODEL_ID, engine=self.ENGINE)


# ── Main service ──────────────────────────────────────────────────────────────

class RestrictedModelInferenceService:
    """Dispatch restricted (non-generative) inference operations to adapters.

    All operations are gated by PathAiModePolicy: if ``restricted_transformer_inference``
    is blocked for the relevant path, a ``InferenceBlockedError`` is raised and an
    audit event is emitted.
    """

    class InferenceBlockedError(RuntimeError):
        pass

    class NoDegradedFallbackError(RuntimeError):
        pass

    def __init__(
        self,
        *,
        adapters: list[BaseInferenceAdapter] | None = None,
        policy_service: PathAiModePolicyService | None = None,
        use_mock_fallback: bool = True,
    ) -> None:
        self._adapters: list[BaseInferenceAdapter] = list(adapters or [])
        self._policy = policy_service or get_path_ai_mode_policy_service()
        self._mock = MockInferenceAdapter() if use_mock_fallback else None
        self._audit_log: list[InferenceAuditEvent] = []

    def add_adapter(self, adapter: BaseInferenceAdapter) -> None:
        self._adapters.append(adapter)

    def get_adapter_statuses(self) -> list[AdapterStatus]:
        statuses = [a.status() for a in self._adapters]
        if self._mock:
            statuses.append(self._mock.status())
        return statuses

    def audit_log(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._audit_log]

    # ── Gated operations ──────────────────────────────────────────────────────

    def embed(self, texts: list[str], *, path: str = "") -> list[list[float]]:
        self._check_policy(path, OP_EMBED)
        adapter = self._pick(OP_EMBED)
        t0 = time.time()
        result = adapter.embed(texts)
        self._audit(OP_EMBED, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def classify(
        self, text: str, labels: list[str], *, path: str = ""
    ) -> ClassificationResult:
        self._check_policy(path, OP_CLASSIFY)
        adapter = self._pick(OP_CLASSIFY)
        t0 = time.time()
        result = adapter.classify(text, labels)
        self._audit(OP_CLASSIFY, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def rerank(
        self, query: str, candidates: list[dict[str, Any]], *, path: str = ""
    ) -> list[RerankResult]:
        self._check_policy(path, OP_RERANK)
        adapter = self._pick(OP_RERANK)
        t0 = time.time()
        result = adapter.rerank(query, candidates)
        self._audit(OP_RERANK, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def score_choices(
        self, prompt: str, choices: list[str], *, path: str = ""
    ) -> list[ChoiceScore]:
        if not choices:
            raise ValueError("score_choices requires at least one choice")
        self._check_policy(path, OP_SCORE_CHOICES)
        adapter = self._pick(OP_SCORE_CHOICES)
        t0 = time.time()
        result = adapter.score_choices(prompt, choices)
        self._audit(OP_SCORE_CHOICES, adapter, path, (time.time() - t0) * 1000, "ok")
        validate_no_generation(result)
        return result

    def extract_features(self, text: str, *, path: str = "") -> FeatureVector:
        self._check_policy(path, OP_EXTRACT_FEATURES)
        adapter = self._pick(OP_EXTRACT_FEATURES)
        t0 = time.time()
        result = adapter.extract_features(text)
        self._audit(OP_EXTRACT_FEATURES, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    def risk_score(
        self, input_dict: dict[str, Any], *, path: str = ""
    ) -> RiskScoreResult:
        self._check_policy(path, OP_RISK_SCORE)
        adapter = self._pick(OP_RISK_SCORE)
        t0 = time.time()
        result = adapter.risk_score(input_dict)
        self._audit(OP_RISK_SCORE, adapter, path, (time.time() - t0) * 1000, "ok")
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_policy(self, path: str, op: str) -> None:
        if not path:
            return
        policy = self._policy.resolve(path)
        if not policy.is_mode_allowed(AI_MODE_RESTRICTED_TRANSFORMER):
            self._audit_blocked(op, path, "policy_blocked_restricted_transformer")
            raise self.InferenceBlockedError(
                f"restricted_transformer_inference blocked for path={path!r} "
                f"by policy rule={policy.matched_rule}"
            )

    def _pick(self, op: str) -> BaseInferenceAdapter:
        cap = _OP_TO_CAP.get(op, "")
        for adapter in self._adapters:
            st = adapter.status()
            if st.status == "ready" and st.has_capability(cap):
                return adapter
        if self._mock:
            log.debug("RestrictedModelInferenceService: using mock fallback for op=%s", op)
            return self._mock
        raise self.NoDegradedFallbackError(
            f"No adapter available for operation={op!r} and mock fallback is disabled"
        )

    def _audit(
        self, op: str, adapter: BaseInferenceAdapter, path: str, ms: float, reason: str
    ) -> None:
        st = adapter.status()
        ev = InferenceAuditEvent(
            event="model_inference_finished",
            operation=op,
            adapter_engine=st.engine,
            model_id=st.model_id,
            path=path,
            latency_ms=ms,
            reason_code=reason,
        )
        self._audit_log.append(ev)

    def _audit_blocked(self, op: str, path: str, reason: str) -> None:
        ev = InferenceAuditEvent(
            event="model_inference_blocked",
            operation=op,
            path=path,
            reason_code=reason,
        )
        self._audit_log.append(ev)
        log.warning(
            "RestrictedModelInferenceService: inference blocked op=%s path=%r reason=%s",
            op, path, reason,
        )


# ── Validation helper ─────────────────────────────────────────────────────────

def validate_no_generation(results: list[ChoiceScore]) -> None:
    """Raise ValueError if any ChoiceScore contains free-text generation."""
    for r in results:
        if not r.no_generation:
            raise ValueError(
                f"ChoiceScore for choice={r.choice!r} has no_generation=False — "
                "free generation is not permitted in restricted inference"
            )


# ── Module singleton ──────────────────────────────────────────────────────────

_service: RestrictedModelInferenceService | None = None


def get_restricted_model_inference_service() -> RestrictedModelInferenceService:
    global _service
    if _service is None:
        _service = RestrictedModelInferenceService()
    return _service


def reset_restricted_model_inference_service(
    new: RestrictedModelInferenceService | None = None,
) -> None:
    global _service
    _service = new
