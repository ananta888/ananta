"""Confidence-gated cascade over decision providers (DPRV).

``Rules -> Jev -> LLM -> defer``, or any other order from configuration: each
stage answers, and its result is accepted only if every question reaches that
stage's confidence threshold. An error or a result below the threshold moves on
to the next stage; after the last stage the outcome is ``deferred`` and the
caller continues on its existing path (reviewer, human gate, the normal LLM
route). The cascade never approves anything itself: callers use a decision as
a proposal and keep every policy, approval and security gate.

One deadline covers all stages, so a slow provider cannot stretch the call.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from agent.services.decision_providers.base import DecisionProvider
from agent.services.decision_providers.types import DecisionProviderError, DecisionRequest, DecisionResult


@dataclass(frozen=True)
class CascadeStage:
    provider: DecisionProvider
    min_confidence: float = 0.9
    # per-question thresholds override ``min_confidence`` (e.g. a stricter safety question)
    question_thresholds: Mapping[str, float] = field(default_factory=dict)

    def threshold(self, key: str) -> float:
        return float(self.question_thresholds.get(key, self.min_confidence))


@dataclass(frozen=True)
class StageTrace:
    provider_id: str
    outcome: str  # accepted | low_confidence | error | unavailable | skipped_deadline
    reason: str = ""
    latency_ms: float = 0.0
    min_confidence_seen: float | None = None


@dataclass(frozen=True)
class CascadeOutcome:
    result: DecisionResult | None
    trace: tuple[StageTrace, ...]

    @property
    def deferred(self) -> bool:
        return self.result is None

    @property
    def decided_by(self) -> str | None:
        return None if self.result is None else self.result.provider_id

    @property
    def fell_back(self) -> bool:
        """Accepted, but not by the first stage that was tried."""
        return self.result is not None and len(self.trace) > 1

    def to_mapping(self) -> dict[str, Any]:
        return {"deferred": self.deferred, "decided_by": self.decided_by,
                "result": self.result.to_mapping() if self.result else None,
                "trace": [stage.__dict__ for stage in self.trace]}


class DecisionCascade:
    def __init__(self, stages: Sequence[CascadeStage], *, deadline_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not stages:
            raise ValueError("cascade_requires_stages")
        self._stages = tuple(stages)
        self._deadline = float(deadline_seconds)
        self._clock = clock

    @property
    def stages(self) -> tuple[CascadeStage, ...]:
        return self._stages

    def decide(self, request: DecisionRequest) -> CascadeOutcome:
        deadline = self._clock() + self._deadline
        trace: list[StageTrace] = []
        for stage in self._stages:
            provider_id = stage.provider.provider_id
            remaining = deadline - self._clock()
            if remaining <= 0.05:
                trace.append(StageTrace(provider_id, "skipped_deadline"))
                break
            available, reason = stage.provider.is_available()
            if not available:
                trace.append(StageTrace(provider_id, "unavailable", reason))
                continue
            started = self._clock()
            try:
                result = stage.provider.decide(request, timeout_seconds=remaining)
            except DecisionProviderError as error:
                trace.append(StageTrace(provider_id, "error", error.reason_code,
                                        (self._clock() - started) * 1000.0))
                continue
            latency = (self._clock() - started) * 1000.0
            weakest = min(result.answers[q.key].confidence - stage.threshold(q.key) for q in request.questions)
            lowest = min(result.answers[q.key].confidence for q in request.questions)
            if weakest >= 0:
                trace.append(StageTrace(provider_id, "accepted", "", latency, lowest))
                return CascadeOutcome(result, tuple(trace))
            trace.append(StageTrace(provider_id, "low_confidence", "", latency, lowest))
        return CascadeOutcome(None, tuple(trace))
