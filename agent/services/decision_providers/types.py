"""Typed decisions shared by every decision provider (DPRV).

A decision is a closed question about a ``state`` text with one of three kinds,
taken over from TypeSafe's System One vocabulary because it fits Ananta's
gate/router decisions exactly:

- ``choice``: one label out of 2..255 described options;
- ``score``: a position on an ordered rubric of 2..10 levels (0 .. n-1);
- ``noul``: the probability that a statement about the state is true.

Providers (TypeSafe Jev, a local llama.cpp decision server, an LLM prompt,
rules, a mock) all answer with these types, so callers never depend on a
vendor format. A provider only proposes; policy, approval and security gates
stay with the Hub and are never decided here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

KINDS = ("choice", "score", "noul")
MAX_STATE_CHARS = 32_000
MAX_QUESTIONS = 32
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
MAX_TEXT_CHARS = 2_000
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")


class DecisionProviderError(Exception):
    """A decision that could not be obtained. ``reason_code`` is stable and safe to log."""

    def __init__(self, reason_code: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable
        self.status = status


def peak_confidence(probabilities: Mapping[str, float]) -> float:
    """TypeSafe's published statistic ``(n * peak - 1) / (n - 1)``: 0 = uniform, 1 = certain."""
    values = [float(value) for value in probabilities.values()]
    if len(values) < 2:
        return 1.0 if values else 0.0
    return max(0.0, min(1.0, (len(values) * max(values) - 1.0) / (len(values) - 1.0)))


def _text(value: Any, reason: str, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DecisionProviderError(reason)
    return value.strip()


@dataclass(frozen=True)
class DecisionQuestion:
    key: str
    kind: str
    instructions: str
    # choice: (label, description) per option; score: (level name, description) in rubric order
    options: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(str(self.key or "")) or self.kind not in KINDS:
            raise DecisionProviderError("decision_question_invalid")
        _text(self.instructions, "decision_question_instructions_invalid")
        labels = [label for label, _description in self.options]
        if len(set(labels)) != len(labels):
            raise DecisionProviderError("decision_question_options_invalid")
        if self.kind == "choice":
            if not 2 <= len(labels) <= MAX_CHOICE_OPTIONS or not all(_LABEL.fullmatch(label) for label in labels):
                raise DecisionProviderError("decision_question_options_invalid")
        elif self.kind == "score":
            if not 2 <= len(labels) <= MAX_SCORE_LEVELS:
                raise DecisionProviderError("decision_question_options_invalid")
        elif labels:
            raise DecisionProviderError("decision_question_options_invalid")
        for label, description in self.options:
            _text(label, "decision_question_options_invalid", 128)
            _text(description, "decision_question_options_invalid")

    @classmethod
    def choice(cls, key: str, instructions: str, options: Mapping[str, str]) -> DecisionQuestion:
        return cls(key, "choice", instructions, tuple((str(k), str(v)) for k, v in options.items()))

    @classmethod
    def score(cls, key: str, instructions: str, levels: Sequence[str]) -> DecisionQuestion:
        return cls(key, "score", instructions, tuple((str(level), str(level)) for level in levels))

    @classmethod
    def noul(cls, key: str, instructions: str) -> DecisionQuestion:
        return cls(key, "noul", instructions)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for label, _description in self.options)


@dataclass(frozen=True)
class DecisionRequest:
    state: str
    questions: tuple[DecisionQuestion, ...]
    purpose: str = "generic"  # decision area, e.g. "tool_routing"; used for config, audit, metrics

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state.strip() or len(self.state) > MAX_STATE_CHARS:
            raise DecisionProviderError("decision_state_invalid")
        keys = [question.key for question in self.questions]
        if not 1 <= len(keys) <= MAX_QUESTIONS or len(set(keys)) != len(keys):
            raise DecisionProviderError("decision_questions_invalid")

    def question(self, key: str) -> DecisionQuestion:
        for question in self.questions:
            if question.key == key:
                return question
        raise KeyError(key)


@dataclass(frozen=True)
class DecisionAnswer:
    key: str
    kind: str
    choice: str | None = None  # choice: selected label
    score: float | None = None  # score: 0 .. n-1, probability-weighted
    probability: float | None = None  # noul: probability the statement is true
    probabilities: Mapping[str, float] = field(default_factory=dict)  # choice: label -> p; score: level -> p
    confidence: float = 0.0  # 0..1, comparable across kinds (peak statistic; noul: |2p - 1|)

    def __post_init__(self) -> None:
        for value in (self.score, self.probability, self.confidence, *self.probabilities.values()):
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise DecisionProviderError("decision_answer_not_finite")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise DecisionProviderError("decision_answer_confidence_invalid")

    @property
    def level(self) -> int | None:
        """The rubric level a score rounds to."""
        return None if self.score is None else int(round(self.score))

    def to_mapping(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "choice": self.choice, "score": self.score,
                "probability": self.probability, "probabilities": dict(self.probabilities),
                "confidence": self.confidence}


@dataclass(frozen=True)
class DecisionUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class DecisionResult:
    provider_id: str
    model: str
    answers: Mapping[str, DecisionAnswer]
    usage: DecisionUsage = DecisionUsage()
    latency_ms: float = 0.0
    request_id: str | None = None
    attempts: int = 1

    def answer(self, key: str) -> DecisionAnswer:
        return self.answers[key]

    def to_mapping(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "model": self.model, "request_id": self.request_id,
                "latency_ms": round(self.latency_ms, 1), "attempts": self.attempts,
                "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens,
                          "cost_usd": self.usage.cost_usd},
                "answers": {key: answer.to_mapping() for key, answer in self.answers.items()}}
