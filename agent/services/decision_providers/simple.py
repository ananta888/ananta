"""Rule and fixed-answer decision providers (DPRV).

``RulesDecisionProvider`` wraps deterministic rules: a rule answers the
questions it is sure about and abstains (``None``) otherwise, so it can be the
first, free stage of a cascade. ``StaticDecisionProvider`` returns fixed
answers: the deterministic double for headless tests and the "feature off"
baseline.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from agent.services.decision_providers.types import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
)

Rule = Callable[[DecisionRequest], Mapping[str, DecisionAnswer] | None]


class RulesDecisionProvider:
    def __init__(self, rules: list[Rule], *, provider_id: str = "rules") -> None:
        self.provider_id = provider_id
        self._rules = list(rules)

    def is_available(self) -> tuple[bool, str]:
        return (True, "ok") if self._rules else (False, "no_rules")

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        del timeout_seconds
        started = time.monotonic()
        answers: dict[str, DecisionAnswer] = {}
        for rule in self._rules:
            for key, answer in dict(rule(request) or {}).items():
                answers.setdefault(key, answer)
        if set(answers) != {question.key for question in request.questions}:
            raise DecisionProviderError("rules_abstained")
        return DecisionResult(provider_id=self.provider_id, model="rules", answers=answers,
                              latency_ms=(time.monotonic() - started) * 1000.0)


class StaticDecisionProvider:
    def __init__(self, answers: Callable[[DecisionRequest], Mapping[str, DecisionAnswer]] | None = None, *,
                 provider_id: str = "mock", error: DecisionProviderError | None = None) -> None:
        self.provider_id = provider_id
        self._answers = answers
        self._error = error
        self.calls: list[DecisionRequest] = []

    def is_available(self) -> tuple[bool, str]:
        return True, "ok"

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        del timeout_seconds
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        if self._answers is None:
            raise DecisionProviderError("mock_no_answers")
        return DecisionResult(provider_id=self.provider_id, model="mock", answers=dict(self._answers(request)))
