"""TypeSafe Jev (System One) as a decision provider (DPRV).

``POST {base_url}/v1/systemone`` with ``{state, model, questions}``; the answer
maps one to one onto ``DecisionAnswer``. Stdlib HTTP only, so the provider runs
in Hub, worker and host scripts alike; the official ``typesafe-sdk`` is not
required.

Reliability:

- one total deadline per decision; every attempt gets what remains of it;
- 429, 529, 5xx, timeouts and transport errors are retried with capped
  exponential backoff (``retry-after-ms`` honoured within the deadline) under
  one ``Idempotency-Key``; 401/403/422 are never retried;
- the response is validated strictly: exactly the asked keys and kinds, only
  known labels, finite probabilities. Anything else is ``response_invalid``.

Secrets: the key comes from ``TYPESAFE_API_KEY`` or ``TYPESAFE_API_KEY_FILE``
(raw key or ``TYPESAFE_API_KEY=...`` env-file line), is read at call time, and
never appears in results, errors, logs or ``repr``.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
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

PROVIDER_ID = "typesafe_jev"
DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
KEY_FILE_ENV = "TYPESAFE_API_KEY_FILE"
# Published list price (USD per million tokens); configurable, since prices change.
DEFAULT_PRICE_INPUT_PER_MILLION = 0.042
DEFAULT_PRICE_OUTPUT_PER_MILLION = 0.0
MAX_RESPONSE_BYTES = 1024 * 1024
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_NON_RETRYABLE_REASONS = {400: "request_invalid", 401: "auth_invalid", 403: "auth_forbidden",
                          404: "endpoint_not_found", 413: "request_too_large", 422: "request_invalid"}


def read_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    """The key from ``TYPESAFE_API_KEY`` or ``TYPESAFE_API_KEY_FILE``; ``None`` if absent."""
    env = os.environ if environ is None else environ
    direct = str(env.get(KEY_ENV) or "").strip()
    if direct:
        return direct
    path = str(env.get(KEY_FILE_ENV) or "").strip()
    if not path:
        return None
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip().removeprefix("export ").strip() == KEY_ENV:
            return value.strip().strip("'\"") or None
        if not sep:
            return line
    return None


def redact(text: str, secret: str | None) -> str:
    """``text`` with ``secret`` (and bearer tokens in general) removed."""
    value = str(text or "")
    if secret:
        value = value.replace(secret, "[REDACTED]")
    return value


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_initial: float = 0.25
    backoff_max: float = 2.0

    def delay(self, attempt: int, retry_after_ms: float | None = None) -> float:
        if retry_after_ms is not None and retry_after_ms >= 0:
            return min(self.backoff_max, retry_after_ms / 1000.0)
        base = min(self.backoff_max, self.backoff_initial * (2 ** max(0, attempt - 1)))
        return base * (0.5 + random.random() / 2)  # jitter


def _probabilities(raw: Any, allowed: set[str]) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise DecisionProviderError("response_invalid")
    result = {}
    for label, value in raw.items():
        if str(label) not in allowed or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionProviderError("response_invalid")
        value = float(value)
        if not math.isfinite(value) or not -1e-6 <= value <= 1.0 + 1e-6:
            raise DecisionProviderError("response_invalid")
        result[str(label)] = min(1.0, max(0.0, value))
    return result


def _unit(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DecisionProviderError("response_invalid")
    value = float(value)
    if not low - 1e-6 <= value <= high + 1e-6:
        raise DecisionProviderError("response_invalid")
    return min(high, max(low, value))


def parse_answer(question: DecisionQuestion, raw: Any) -> DecisionAnswer:
    """One TypeSafe answer, validated against the question it answers."""
    if not isinstance(raw, Mapping) or raw.get("type") != question.kind:
        raise DecisionProviderError("response_invalid")
    if question.kind == "choice":
        labels = set(question.labels)
        choice = raw.get("choice")
        if choice not in labels:
            raise DecisionProviderError("response_invalid")
        probabilities = _probabilities(raw.get("probabilities") or {}, labels)
        confidence = raw.get("confidence")
        confidence = _unit(confidence) if confidence is not None else peak_confidence(probabilities)
        return DecisionAnswer(question.key, "choice", choice=choice, probabilities=probabilities,
                              confidence=confidence)
    if question.kind == "score":
        levels = question.labels
        by_index = _probabilities(raw.get("probabilities") or {}, {str(i) for i in range(len(levels))})
        probabilities = {levels[int(index)]: value for index, value in by_index.items()}
        score = _unit(raw.get("score"), 0.0, float(len(levels) - 1))
        confidence = raw.get("confidence")
        confidence = _unit(confidence) if confidence is not None else peak_confidence(probabilities)
        return DecisionAnswer(question.key, "score", score=score, probabilities=probabilities,
                              confidence=confidence)
    probability = _unit(raw.get("noul", raw.get("probability")))
    return DecisionAnswer(question.key, "noul", probability=probability,
                          probabilities={"true": probability, "false": 1.0 - probability},
                          confidence=abs(2.0 * probability - 1.0))


def request_body(request: DecisionRequest, model: str) -> dict[str, Any]:
    questions: dict[str, Any] = {}
    for question in request.questions:
        entry: dict[str, Any] = {"type": question.kind, "instructions": question.instructions}
        if question.kind == "choice":
            entry["criteria"] = dict(question.options)
        elif question.kind == "score":
            entry["criteria"] = list(question.labels)
        questions[question.key] = entry
    return {"state": request.state, "model": model, "questions": questions}


def parse_response(request: DecisionRequest, payload: Any) -> tuple[str, dict[str, DecisionAnswer], DecisionUsage]:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("answers"), Mapping):
        raise DecisionProviderError("response_invalid")
    answers_raw = payload["answers"]
    if set(map(str, answers_raw)) != {question.key for question in request.questions}:
        raise DecisionProviderError("response_invalid")
    answers = {question.key: parse_answer(question, answers_raw[question.key]) for question in request.questions}
    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    usage = DecisionUsage(input_tokens=max(0, int(usage_raw.get("input_tokens") or 0)),
                          output_tokens=max(0, int(usage_raw.get("output_tokens") or 0)))
    return str(payload.get("model") or ""), answers, usage


class TypeSafeJevProvider:
    provider_id = PROVIDER_ID

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                 timeout_seconds: float = 10.0, retry: RetryPolicy = RetryPolicy(),
                 api_key: Callable[[], str | None] = read_api_key,
                 price_input_per_million: float = DEFAULT_PRICE_INPUT_PER_MILLION,
                 price_output_per_million: float = DEFAULT_PRICE_OUTPUT_PER_MILLION,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        if not base_url.startswith("https://") and not base_url.startswith(("http://127.0.0.1", "http://localhost")):
            raise DecisionProviderError("base_url_invalid")  # the key only travels over TLS (or to localhost)
        self._url = base_url + "/v1/systemone"
        self._model = str(model or DEFAULT_MODEL)
        self._timeout = max(0.5, float(timeout_seconds))
        self._retry = retry
        self._api_key = api_key
        self._prices = (float(price_input_per_million), float(price_output_per_million))
        self._opener = opener
        self._sleep = sleep
        self._clock = clock

    def __repr__(self) -> str:  # never the key
        return f"TypeSafeJevProvider(url={self._url!r}, model={self._model!r})"

    def is_available(self) -> tuple[bool, str]:
        return (True, "ok") if self._api_key() else (False, "api_key_missing")

    def _post(self, body: bytes, key: str, idempotency_key: str, timeout: float) -> tuple[Any, str | None]:
        request = urllib.request.Request(self._url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json",
            "Idempotency-Key": idempotency_key, "User-Agent": "ananta-decision-provider/1"})
        with self._opener(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            request_id = response.headers.get("x-typesafe-request-id") if response.headers else None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DecisionProviderError("response_too_large")
        try:
            return json.loads(raw), request_id
        except ValueError:
            raise DecisionProviderError("response_invalid") from None

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        key = self._api_key()
        if not key:
            raise DecisionProviderError("api_key_missing")
        body = json.dumps(request_body(request, self._model)).encode("utf-8")
        idempotency_key = uuid.uuid4().hex
        started = self._clock()
        deadline = started + (float(timeout_seconds) if timeout_seconds else self._timeout)
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - self._clock()
            if remaining <= 0.05:
                raise DecisionProviderError("timeout", retryable=True)
            retry_after_ms = None
            try:
                payload, request_id = self._post(body, key, idempotency_key, remaining)
                model, answers, usage = parse_response(request, payload)
                cost = (usage.input_tokens * self._prices[0] + usage.output_tokens * self._prices[1]) / 1e6
                return DecisionResult(
                    provider_id=self.provider_id, model=model or self._model, answers=answers,
                    usage=DecisionUsage(usage.input_tokens, usage.output_tokens, round(cost, 9)),
                    latency_ms=(self._clock() - started) * 1000.0, request_id=request_id, attempts=attempt)
            except urllib.error.HTTPError as error:
                status = int(error.code or 0)
                if status in _NON_RETRYABLE_REASONS or (400 <= status < 500 and status not in _RETRYABLE_STATUS):
                    raise DecisionProviderError(_NON_RETRYABLE_REASONS.get(status, f"http_{status}"),
                                                status=status) from None
                failure = DecisionProviderError("rate_limited" if status == 429 else "upstream_busy"
                                                if status == 529 else f"http_{status}", retryable=True, status=status)
                header = error.headers.get("retry-after-ms") if error.headers else None
                try:
                    retry_after_ms = float(header) if header is not None else None
                except ValueError:
                    retry_after_ms = None
            except DecisionProviderError:
                raise
            except TimeoutError:
                failure = DecisionProviderError("timeout", retryable=True)
            except (urllib.error.URLError, OSError):
                failure = DecisionProviderError("transport_error", retryable=True)
            if attempt > self._retry.max_retries:
                raise failure
            delay = self._retry.delay(attempt, retry_after_ms)
            if self._clock() + delay >= deadline:
                raise failure
            self._sleep(delay)
