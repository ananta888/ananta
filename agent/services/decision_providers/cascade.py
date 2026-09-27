"""Confidence-gated cascade over decision providers (DPRV).

``Rules -> Jev -> LLM -> defer``, or any other order from configuration: each
stage answers, and its result is accepted only if every question reaches that
stage's confidence threshold. An error or a result below the threshold moves on
to the next stage; after the last stage the outcome is ``deferred`` and the
caller continues on its existing path (reviewer, human gate, the normal LLM
route). The cascade never approves anything itself: callers use a decision as
a proposal and keep every policy, approval and security gate.

Two kinds of stage:

- ``CascadeStage``: one provider;
- ``AgreementStage``: several independent providers answer the same request in
  parallel; it is accepted only if all give the same answer, each above the
  threshold (``disagreement`` otherwise). Two different models have to be
  wrong -- or manipulated -- the same way at once, which is what makes a local
  model plus TypeSafe Jev worth more than either alone (e.g. against prompt
  injection). Latency is the slowest member, not the sum.

One deadline covers all stages, so a slow provider cannot stretch the call.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.services.decision_providers.base import DecisionProvider
from agent.services.decision_providers.types import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
)


@dataclass(frozen=True)
class StageTrace:
    provider_id: str
    outcome: str  # accepted | low_confidence | disagreement | error | unavailable | skipped_deadline
    reason: str = ""
    latency_ms: float = 0.0
    min_confidence_seen: float | None = None


class Stage(Protocol):
    stage_id: str

    def attempt(self, request: DecisionRequest, timeout: float) -> tuple[StageTrace, DecisionResult | None]: ...


@dataclass(frozen=True)
class _Thresholds:
    min_confidence: float = 0.9
    question_thresholds: Mapping[str, float] = field(default_factory=dict)

    def threshold(self, key: str) -> float:
        return float(self.question_thresholds.get(key, self.min_confidence))

    def shortfall(self, request: DecisionRequest, result: DecisionResult) -> tuple[float, float]:
        """``(weakest margin over the thresholds, lowest confidence)``."""
        weakest = min(result.answers[q.key].confidence - self.threshold(q.key) for q in request.questions)
        return weakest, min(result.answers[q.key].confidence for q in request.questions)


class CascadeStage:
    def __init__(self, provider: DecisionProvider, min_confidence: float = 0.9,
                 question_thresholds: Mapping[str, float] | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.provider = provider
        self.stage_id = provider.provider_id
        self._thresholds = _Thresholds(float(min_confidence), dict(question_thresholds or {}))
        self._clock = clock

    @property
    def min_confidence(self) -> float:
        return self._thresholds.min_confidence

    def threshold(self, key: str) -> float:
        return self._thresholds.threshold(key)

    def attempt(self, request: DecisionRequest, timeout: float) -> tuple[StageTrace, DecisionResult | None]:
        available, reason = self.provider.is_available()
        if not available:
            return StageTrace(self.stage_id, "unavailable", reason), None
        started = self._clock()
        try:
            result = self.provider.decide(request, timeout_seconds=timeout)
        except DecisionProviderError as error:
            return StageTrace(self.stage_id, "error", error.reason_code, (self._clock() - started) * 1000.0), None
        latency = (self._clock() - started) * 1000.0
        weakest, lowest = self._thresholds.shortfall(request, result)
        if weakest >= 0:
            return StageTrace(self.stage_id, "accepted", "", latency, lowest), result
        return StageTrace(self.stage_id, "low_confidence", "", latency, lowest), None


def same_answer(first: DecisionAnswer, second: DecisionAnswer) -> bool:
    """Two answers to one question agree: same label, same side of 0.5, or same rubric level."""
    if first.kind != second.kind:
        return False
    if first.kind == "choice":
        return first.choice == second.choice
    if first.kind == "noul":
        return (float(first.probability or 0.0) >= 0.5) == (float(second.probability or 0.0) >= 0.5)
    return first.level == second.level


def merge_agreeing(results: Sequence[DecisionResult], stage_id: str) -> DecisionResult:
    """One result for agreeing members: the first member's answers at the lowest member confidence."""
    first = results[0]
    answers = {}
    for key, answer in first.answers.items():
        confidence = min(result.answers[key].confidence for result in results)
        answers[key] = DecisionAnswer(answer.key, answer.kind, choice=answer.choice, score=answer.score,
                                      probability=answer.probability, probabilities=dict(answer.probabilities),
                                      confidence=confidence)
    usage = DecisionUsage(sum(r.usage.input_tokens for r in results), sum(r.usage.output_tokens for r in results),
                          round(sum(r.usage.cost_usd for r in results), 9))
    return DecisionResult(provider_id=stage_id, model="+".join(r.model for r in results), answers=answers,
                          usage=usage, latency_ms=max(r.latency_ms for r in results),
                          request_id=",".join(r.request_id for r in results if r.request_id) or None)


class AgreementStage:
    def __init__(self, providers: Sequence[DecisionProvider], min_confidence: float = 0.9,
                 question_thresholds: Mapping[str, float] | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if len(providers) < 2:
            raise ValueError("agreement_requires_two_providers")
        self.providers = tuple(providers)
        self.stage_id = "agreement(" + "+".join(p.provider_id for p in self.providers) + ")"
        self._thresholds = _Thresholds(float(min_confidence), dict(question_thresholds or {}))
        self._clock = clock

    def attempt(self, request: DecisionRequest, timeout: float) -> tuple[StageTrace, DecisionResult | None]:
        for provider in self.providers:
            available, reason = provider.is_available()
            if not available:
                return StageTrace(self.stage_id, "unavailable", f"{provider.provider_id}:{reason}"), None
        started = self._clock()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.providers)) as pool:
            futures = [pool.submit(p.decide, request, timeout_seconds=timeout) for p in self.providers]
            results: list[DecisionResult] = []
            for provider, future in zip(self.providers, futures):
                try:
                    results.append(future.result())
                except DecisionProviderError as error:
                    return (StageTrace(self.stage_id, "error", f"{provider.provider_id}:{error.reason_code}",
                                       (self._clock() - started) * 1000.0), None)
        latency = (self._clock() - started) * 1000.0
        lowest = min(self._thresholds.shortfall(request, r)[1] for r in results)
        for question in request.questions:
            answers = [r.answers[question.key] for r in results]
            if not all(same_answer(answers[0], other) for other in answers[1:]):
                return StageTrace(self.stage_id, "disagreement", question.key, latency, lowest), None
        if min(self._thresholds.shortfall(request, r)[0] for r in results) < 0:
            return StageTrace(self.stage_id, "low_confidence", "", latency, lowest), None
        return StageTrace(self.stage_id, "accepted", "", latency, lowest), merge_agreeing(results, self.stage_id)


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

    @property
    def disagreed(self) -> bool:
        return any(stage.outcome == "disagreement" for stage in self.trace)

    def to_mapping(self) -> dict[str, Any]:
        return {"deferred": self.deferred, "decided_by": self.decided_by,
                "result": self.result.to_mapping() if self.result else None,
                "trace": [stage.__dict__ for stage in self.trace]}


class DecisionCascade:
    def __init__(self, stages: Sequence[Stage], *, deadline_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not stages:
            raise ValueError("cascade_requires_stages")
        self._stages = tuple(stages)
        self._deadline = float(deadline_seconds)
        self._clock = clock

    @property
    def stages(self) -> tuple[Stage, ...]:
        return self._stages

    def decide(self, request: DecisionRequest) -> CascadeOutcome:
        deadline = self._clock() + self._deadline
        trace: list[StageTrace] = []
        for stage in self._stages:
            remaining = deadline - self._clock()
            if remaining <= 0.05:
                trace.append(StageTrace(stage.stage_id, "skipped_deadline"))
                break
            stage_trace, result = stage.attempt(request, remaining)
            trace.append(stage_trace)
            if result is not None:
                return CascadeOutcome(result, tuple(trace))
        return CascadeOutcome(None, tuple(trace))
