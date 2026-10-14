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
import math
import hashlib
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from agent.services.decision_providers.local_http import NoRedirect, default_local_hosts, validate_local_endpoint

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


def request_body(request: DecisionRequest, model: str = "", *, scoped_prefix_cache: bool = False) -> dict[str, Any]:
    if any(q.dependency_mode != "independent" or q.depends_on for q in request.questions):
        raise DecisionProviderError("parallel_dependencies_require_system2")
    body = {"model": model, "instructions": INSTRUCTIONS,
            "schema": {"type": "object", "properties": {q.key: _field(q) for q in request.questions}},
            "contexts": [request.state], "mode": "tree", "return_probs": True,
            "cache_context": False, "cache_prompt": False, "cache_media": False, "share_tokens": True}
    if scoped_prefix_cache:
        if not request.tenant_id or not request.subject_id:
            raise DecisionProviderError("cache_scope_required")
        binding = {"tenant": request.tenant_id, "subject": request.subject_id, "model": model,
                   "purpose": request.purpose, "schema": body["schema"]}
        body["cache_scope"] = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        body["cache_prompt"] = True
    return body


def _distribution(raw: Mapping[str, Any], allowed: list[Any]) -> dict[Any, float]:
    probs = raw.get("probs")
    if not isinstance(probs, list):
        raise DecisionProviderError("response_invalid")
    result: dict[Any, float] = {}
    for item in probs:
        value, probability = (item.get("value"), item.get("probability")) if isinstance(item, Mapping) else (None, None)
        if (not any(type(value) is type(a) and value == a for a in allowed)
                or value in result or isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability) or not 0 <= probability <= 1):
            raise DecisionProviderError("response_invalid")
        result[value] = max(0.0, min(1.0, float(probability)))
    total = sum(result.values())
    if set(result) != set(allowed) or total <= 0 or abs(total - 1.0) > 0.01:
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
                 opener: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 max_inflight: int = 4, require_cache_isolation: bool = False,
                 scoped_prefix_cache: bool = False) -> None:
        base_url = str(base_url or "").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise DecisionProviderError("base_url_invalid")
        validate_local_endpoint(base_url, default_local_hosts(base_url), require_port=False)
        self._base = base_url
        self._model = str(model or "")
        self._timeout = max(0.5, float(timeout_seconds))
        self._opener = opener or urllib.request.build_opener(NoRedirect()).open
        self._clock = clock
        if type(max_inflight) is not int or not 1 <= max_inflight <= 64:
            raise DecisionProviderError("decision_admission_invalid")
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._require_cache_isolation = require_cache_isolation
        self._scoped_prefix_cache = scoped_prefix_cache

    def is_available(self) -> tuple[bool, str]:
        # The inference request owns the deadline. A synchronous health request
        # before every call would consume an extra, unbudgeted two seconds.
        return True, "configured"

    def probe_health(self) -> tuple[bool, str]:
        try:
            with self._opener(urllib.request.Request(self._base + "/health"), timeout=2.0) as response:
                return (True, "ok") if response.status == 200 else (False, "server_unhealthy")
        except (urllib.error.URLError, OSError):
            return False, "server_unreachable"

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        return self.decide_batch((request,), timeout_seconds=timeout_seconds)[0]

    def decide_batch(self, requests: tuple[DecisionRequest, ...], *,
                     timeout_seconds: float | None = None) -> tuple[DecisionResult, ...]:
        if not 1 <= len(requests) <= 16:
            raise DecisionProviderError("decision_batch_invalid")
        first = requests[0]
        if any(r.questions != first.questions for r in requests):
            raise DecisionProviderError("decision_batch_schema_mismatch")
        if len(requests) > 1 and (not first.tenant_id or not first.subject_id or
                any((r.tenant_id, r.subject_id) != (first.tenant_id, first.subject_id) for r in requests)):
            raise DecisionProviderError("decision_batch_scope_mismatch")
        body = request_body(first, self._model, scoped_prefix_cache=self._scoped_prefix_cache)
        body["contexts"] = [request.state for request in requests]
        if not self._slots.acquire(blocking=False):
            raise DecisionProviderError("queue_full", retryable=True)
        try:
            return self._decide_batch(requests, body, timeout_seconds=timeout_seconds)
        finally:
            self._slots.release()

    def _decide_batch(self, requests, body, *, timeout_seconds):
        started = self._clock()
        expected_cache_scope = body.get("cache_scope")
        body = json.dumps(body, allow_nan=False).encode("utf-8")
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
            rows = payload["results"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise DecisionProviderError("response_invalid") from None
        if not isinstance(rows, list) or len(rows) != len(requests):
            raise DecisionProviderError("response_invalid")
        cache_policy = payload.get("cache_policy")
        cache_verified = (isinstance(cache_policy, Mapping)
            and cache_policy.get("context") is False and cache_policy.get("media") is False
            and (cache_policy.get("prompt") is False or (expected_cache_scope
                and payload.get("cache_scope") == expected_cache_scope and cache_policy.get("prompt") is True)))
        if self._scoped_prefix_cache and not cache_verified:
            raise DecisionProviderError("cache_isolation_unverified")
        if self._require_cache_isolation and not cache_verified:
            raise DecisionProviderError("cache_isolation_unverified")
        results = []
        for request, row in zip(requests, rows):
            fields = row.get("fields") if isinstance(row, Mapping) else None
            if not isinstance(fields, Mapping) or set(fields) != {q.key for q in request.questions}:
                raise DecisionProviderError("response_invalid")
            answers = {q.key: parse_answer(q, fields[q.key]) for q in request.questions}
            diagnostics = {"scoring": {key: {name: value for name, value in raw.items()
                                             if name in {"scored_nodes", "tree", "margin", "entropy", "temperature"}
                                             and isinstance(value, (int, float, bool)) and math.isfinite(value)}
                                       for key, raw in fields.items()},
                           "context_cache_requested": False, "prompt_cache_requested": False,
                           "cache_policy_verified": cache_verified,
                           "raw_logits": "not_exposed_by_runtime"}
            results.append(DecisionResult(provider_id=self.provider_id, model=str(payload.get("model") or self._model),
                                          answers=answers, latency_ms=(self._clock() - started) * 1000.0,
                                          diagnostics=diagnostics))
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        from dataclasses import replace
        try:
            tokens = int(usage.get("prompt_tokens", usage.get("context_tokens", 0)) or 0)
        except (ValueError, TypeError, OverflowError):
            raise DecisionProviderError("response_invalid") from None
        if tokens < 0:
            raise DecisionProviderError("response_invalid")
        # Aggregate batch usage belongs to its first result; never multiply it
        # by the number of contexts when benchmark/report code sums results.
        results[0] = replace(results[0], usage=DecisionUsage(input_tokens=tokens),
                             diagnostics={**results[0].diagnostics, "cached_tokens": usage.get("cached_tokens", 0)})
        return tuple(results)
