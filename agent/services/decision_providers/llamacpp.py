"""The local llama.cpp decision server (``POST /v1/decision``) as a decision provider (DPRV).

The same inference pattern as Jev on an open-weight model on local hardware
(``docs/jev-llamacpp-decision-mode.md``): every question becomes one schema
field whose allowed values are scored in one pass, with the full distribution
(``return_probs``). Mapping onto the typed kinds:

- ``choice`` -> ``enum`` over the labels; option descriptions go into the field
  description, since enum values carry none;
- ``score``  -> ``enum`` over the rubric levels; ``score`` is the expected level
  index, as TypeSafe reports it;
- ``noul``   -> ``boolean``; ``probability`` is ``p(true)``.

Local-only by default: no data leaves the machine, no per-call cost. The
context is never cached across requests (tenant isolation).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from agent.services.decision_providers.types import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
    peak_confidence,
)

PROVIDER_ID = "llamacpp_decision"
INSTRUCTIONS = ("Read the context and answer every field. Each field is an independent question; "
                "choose the value that is correct for the context.")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _field(question: DecisionQuestion) -> dict[str, Any]:
    if question.kind == "noul":
        return {"type": "boolean", "description": f"True if: {question.instructions}"}
    options = "; ".join(f"{label} = {description}" for label, description in question.options
                        if description != label)
    description = question.instructions + (f" Options: {options}." if options else "")
    return {"enum": list(question.labels), "description": description}


def request_body(request: DecisionRequest, model: str = "") -> dict[str, Any]:
    return {"model": model, "instructions": INSTRUCTIONS,
            "schema": {"type": "object", "properties": {q.key: _field(q) for q in request.questions}},
            "contexts": [request.state], "mode": "tree", "return_probs": True, "cache_context": False}


def _distribution(raw: Mapping[str, Any], allowed: list[Any]) -> dict[Any, float]:
    probs = raw.get("probs")
    if not isinstance(probs, list):
        raise DecisionProviderError("response_invalid")
    result: dict[Any, float] = {}
    for item in probs:
        value, probability = (item.get("value"), item.get("probability")) if isinstance(item, Mapping) else (None, None)
        if value not in allowed or isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise DecisionProviderError("response_invalid")
        result[value] = max(0.0, min(1.0, float(probability)))
    total = sum(result.values())
    if total <= 0:
        raise DecisionProviderError("response_invalid")
    return {value: probability / total for value, probability in result.items()}


def parse_answer(question: DecisionQuestion, raw: Any) -> DecisionAnswer:
    if not isinstance(raw, Mapping):
        raise DecisionProviderError("response_invalid")
    if question.kind == "noul":
        dist = _distribution(raw, [True, False])
        p_true = dist.get(True, 0.0)
        return DecisionAnswer(question.key, "noul", probability=p_true,
                              probabilities={"true": p_true, "false": 1.0 - p_true},
                              confidence=abs(2.0 * p_true - 1.0))
    labels = list(question.labels)
    dist = {str(label): dist_p for label, dist_p in _distribution(raw, labels).items()}
    probabilities = {label: dist.get(label, 0.0) for label in labels}
    confidence = peak_confidence(probabilities)
    if question.kind == "choice":
        choice = max(probabilities, key=probabilities.get)
        return DecisionAnswer(question.key, "choice", choice=choice, probabilities=probabilities,
                              confidence=confidence)
    score = sum(index * probabilities[label] for index, label in enumerate(labels))
    return DecisionAnswer(question.key, "score", score=score, probabilities=probabilities, confidence=confidence)


class LlamaCppDecisionProvider:
    provider_id = PROVIDER_ID

    def __init__(self, *, base_url: str, model: str = "", timeout_seconds: float = 20.0,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 clock: Callable[[], float] = time.monotonic) -> None:
        base_url = str(base_url or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise DecisionProviderError("base_url_invalid")
        self._base = base_url
        self._model = str(model or "")
        self._timeout = max(0.5, float(timeout_seconds))
        self._opener = opener
        self._clock = clock

    def is_available(self) -> tuple[bool, str]:
        try:
            with self._opener(urllib.request.Request(self._base + "/health"), timeout=2.0) as response:
                return (True, "ok") if response.status == 200 else (False, "server_unhealthy")
        except (urllib.error.URLError, OSError):
            return False, "server_unreachable"

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        started = self._clock()
        body = json.dumps(request_body(request, self._model)).encode("utf-8")
        http_request = urllib.request.Request(self._base + "/v1/decision", data=body, method="POST",
                                              headers={"Content-Type": "application/json"})
        try:
            with self._opener(http_request, timeout=float(timeout_seconds or self._timeout)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise DecisionProviderError(f"http_{error.code}", retryable=error.code >= 500,
                                        status=error.code) from None
        except TimeoutError:
            raise DecisionProviderError("timeout", retryable=True) from None
        except (urllib.error.URLError, OSError):
            raise DecisionProviderError("transport_error", retryable=True) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DecisionProviderError("response_too_large")
        try:
            payload = json.loads(raw)
            fields = payload["results"][0]["fields"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise DecisionProviderError("response_invalid") from None
        if not isinstance(fields, Mapping) or set(fields) != {q.key for q in request.questions}:
            raise DecisionProviderError("response_invalid")
        answers = {q.key: parse_answer(q, fields[q.key]) for q in request.questions}
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        return DecisionResult(provider_id=self.provider_id, model=str(payload.get("model") or self._model),
                              answers=answers,
                              usage=DecisionUsage(input_tokens=int(usage.get("prompt_tokens") or 0)
                                                  + int(usage.get("context_tokens") or 0)),
                              latency_ms=(self._clock() - started) * 1000.0)
