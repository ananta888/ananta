"""A chat LLM as a decision provider (DPRV): the existing "System 2" way of deciding.

The model gets the questions as a JSON task and must answer
``{"<key>": {"value": ..., "confidence": 0..1}}``. The answer is validated
against the closed label sets exactly like every other provider; anything
outside them is ``response_invalid``, never guessed. The confidence is the
model's own statement: it is *not* calibrated, and the benchmark reports it as
such.

The transport is a port (``complete(prompt, timeout_seconds) -> str``), so the
Hub can pass its configured LLM integration and benchmarks an
OpenAI-compatible endpoint (``OpenAICompatibleCompletion``).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from agent.services.decision_providers.types import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
)

PROVIDER_ID = "llm"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class CompletionTransport(Protocol):
    def complete(self, prompt: str, *, timeout_seconds: float) -> tuple[str, int, int]:
        """``(text, input_tokens, output_tokens)``."""
        ...


def build_prompt(request: DecisionRequest) -> str:
    lines = ["Decide the following questions about the STATE. Answer with one JSON object only, no prose.",
             'Format: {"<question key>": {"value": <answer>, "confidence": <0..1>}}', "", "QUESTIONS:"]
    for question in request.questions:
        if question.kind == "choice":
            options = ", ".join(f'"{label}" ({description})' for label, description in question.options)
            lines.append(f'- {question.key} (choose exactly one of: {options}): {question.instructions}')
        elif question.kind == "score":
            levels = ", ".join(f"{index} = {label}" for index, label in enumerate(question.labels))
            lines.append(f"- {question.key} (integer level: {levels}): {question.instructions}")
        else:
            lines.append(f"- {question.key} (true or false): {question.instructions}")
    lines += ["", "STATE:", request.state]
    return "\n".join(lines)


def _confidence(raw: Mapping[str, Any]) -> float:
    value = raw.get("confidence", 1.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return max(0.0, min(1.0, float(value)))


def parse_answer(question: DecisionQuestion, raw: Any) -> DecisionAnswer:
    if not isinstance(raw, Mapping):
        raw = {"value": raw}
    value, confidence = raw.get("value"), _confidence(raw)
    if question.kind == "choice":
        if value not in question.labels:
            raise DecisionProviderError("response_invalid")
        others = (1.0 - confidence) / (len(question.labels) - 1)
        probabilities = {label: (confidence if label == value else others) for label in question.labels}
        return DecisionAnswer(question.key, "choice", choice=value, probabilities=probabilities,
                              confidence=confidence)
    if question.kind == "score":
        labels = question.labels
        if isinstance(value, str) and value in labels:
            value = labels.index(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= len(labels) - 1:
            raise DecisionProviderError("response_invalid")
        return DecisionAnswer(question.key, "score", score=float(value), confidence=confidence)
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no"}:
        value = value.strip().lower() in {"true", "yes"}
    if not isinstance(value, bool):
        raise DecisionProviderError("response_invalid")
    probability = 0.5 + (confidence / 2.0 if value else -confidence / 2.0)
    return DecisionAnswer(question.key, "noul", probability=probability,
                          probabilities={"true": probability, "false": 1.0 - probability}, confidence=confidence)


def parse_response(request: DecisionRequest, text: str) -> dict[str, DecisionAnswer]:
    match = _JSON_OBJECT.search(str(text or ""))
    try:
        payload = json.loads(match.group(0)) if match else None
    except ValueError:
        payload = None
    if not isinstance(payload, Mapping):
        raise DecisionProviderError("response_invalid")
    missing = [q.key for q in request.questions if q.key not in payload]
    if missing:
        raise DecisionProviderError("response_invalid")
    return {q.key: parse_answer(q, payload[q.key]) for q in request.questions}


class LLMDecisionProvider:
    provider_id = PROVIDER_ID

    def __init__(self, transport: CompletionTransport, *, model: str = "", timeout_seconds: float = 60.0,
                 price_input_per_million: float = 0.0, price_output_per_million: float = 0.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._transport = transport
        self._model = model
        self._timeout = float(timeout_seconds)
        self._prices = (float(price_input_per_million), float(price_output_per_million))
        self._clock = clock

    def is_available(self) -> tuple[bool, str]:
        return True, "ok"

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        started = self._clock()
        text, input_tokens, output_tokens = self._transport.complete(
            build_prompt(request), timeout_seconds=float(timeout_seconds or self._timeout))
        answers = parse_response(request, text)
        cost = (input_tokens * self._prices[0] + output_tokens * self._prices[1]) / 1e6
        return DecisionResult(provider_id=self.provider_id, model=self._model, answers=answers,
                              usage=DecisionUsage(input_tokens, output_tokens, round(cost, 9)),
                              latency_ms=(self._clock() - started) * 1000.0)


class OpenAICompatibleCompletion:
    """``POST {base_url}/chat/completions`` (local llama-server, LM Studio, ...), temperature 0."""

    def __init__(self, *, base_url: str, model: str = "", api_key: str | None = None,
                 opener: Callable[..., Any] = urllib.request.urlopen, max_tokens: int = 400) -> None:
        self._url = str(base_url).rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._opener = opener
        self._max_tokens = int(max_tokens)

    def complete(self, prompt: str, *, timeout_seconds: float) -> tuple[str, int, int]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"model": self._model, "temperature": 0, "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}]}
        request = urllib.request.Request(self._url, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with self._opener(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            raise DecisionProviderError(f"http_{error.code}", retryable=error.code >= 500) from None
        except TimeoutError:
            raise DecisionProviderError("timeout", retryable=True) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise DecisionProviderError("transport_error", retryable=True) from None
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise DecisionProviderError("response_invalid") from None
        usage = payload.get("usage") or {}
        return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
