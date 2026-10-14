"""Local Jeff adapter; wire compatibility is composed with the TypeSafe transport.

Questions are sent separately, with one shared deadline. The adapter never
grants permissions, trains models, follows redirects, or contacts a cloud URL.
"""

from __future__ import annotations

import math
import hashlib
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import replace

from agent.services.decision_providers.types import (
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
)
from agent.services.decision_providers.typesafe import RetryPolicy, TypeSafeJevProvider
from agent.services.decision_providers.local_http import NoRedirect, validate_local_endpoint


class JeffDecisionProvider:
    provider_id = "jeff_gliformer"

    def __init__(
        self,
        *,
        base_url: str,
        model: str = "gliformer-large-v1",
        model_revision: str,
        allowed_hosts: Sequence[str] = ("127.0.0.1", "localhost", "jeff"),
        api_key: Callable[[], str | None],
        timeout_seconds: float = 5,
        max_inflight: int = 4,
        max_state_chars: int = 8000,
        failure_limit: int = 3,
        reset_seconds: float = 30,
        opener=None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 1,
    ) -> None:
        if (
            not isinstance(model_revision, str)
            or len(model_revision) != 40
            or any(c not in "0123456789abcdef" for c in model_revision)
            or type(max_inflight) is not int
            or not 1 <= max_inflight <= 64
            or not 1 <= max_state_chars <= 32000
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
            or not 1 <= failure_limit <= 10
            or not 0 < reset_seconds <= 300
        ):
            raise DecisionProviderError("jeff_config_invalid")
        validate_local_endpoint(base_url, allowed_hosts)
        self.model_revision = model_revision
        self._model = model
        self._clock, self._timeout = clock, timeout_seconds
        self._max_chars = max_state_chars
        self._slots = threading.BoundedSemaphore(max_inflight)
        self._lock = threading.Lock()
        self._failures, self._open_until = 0, 0.0
        self._failure_limit, self._reset_seconds = failure_limit, reset_seconds
        self._wire = TypeSafeJevProvider(
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            retry=RetryPolicy(max_retries=max_retries),
            api_key=api_key,
            price_input_per_million=0,
            price_output_per_million=0,
            opener=opener or urllib.request.build_opener(NoRedirect()).open,
            clock=clock,
            sleep=sleep,
            endpoint_validator=lambda url: validate_local_endpoint(url, allowed_hosts),
            response_validator=self._verify_model_revision,
            headers_for_request=lambda request: (
                {"X-Ananta-Assignment": request.assignment_token} if request.assignment_token else {}
            ),
        )

    def _verify_model_revision(self, headers, request_headers) -> None:
        if headers.get("x-ananta-model-revision") != self.model_revision:
            raise DecisionProviderError("model_revision_unverified")
        token = request_headers.get("X-Ananta-Assignment")
        if token and headers.get("x-ananta-assignment-digest") != hashlib.sha256(token.encode()).hexdigest():
            raise DecisionProviderError("assignment_response_binding_mismatch")

    def is_available(self) -> tuple[bool, str]:
        with self._lock:
            if self._clock() < self._open_until:
                return False, "circuit_open"
        return self._wire.is_available()

    def decide(self, request: DecisionRequest, *, timeout_seconds: float | None = None) -> DecisionResult:
        if any(q.dependency_mode != "independent" or q.depends_on for q in request.questions):
            raise DecisionProviderError("parallel_dependencies_require_system2")
        if len(request.state) > self._max_chars:
            raise DecisionProviderError("request_too_large")
        if any(len(q.labels) > 64 for q in request.questions):
            raise DecisionProviderError("jeff_label_limit")
        available, reason = self.is_available()
        if not available:
            raise DecisionProviderError(reason)
        if not self._slots.acquire(blocking=False):
            raise DecisionProviderError("queue_full", retryable=True)
        started = self._clock()
        deadline = started + min(self._timeout, timeout_seconds if timeout_seconds is not None else self._timeout)
        results = []
        try:
            parts = (
                (request,)
                if request.assignment_token
                else tuple(replace(request, questions=(question,)) for question in request.questions)
            )
            for part in parts:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise DecisionProviderError("deadline_exceeded")
                result = self._wire.decide(part, timeout_seconds=remaining)
                if result.model != self._model:
                    raise DecisionProviderError("model_not_allowed")
                # The score is the upstream raw-distribution expectation; do
                # not reconstruct it from temperature-transformed probabilities.
                for question in part.questions:
                    answer = result.answers[question.key]
                    if question.kind != "noul" and (
                        set(answer.probabilities) != set(question.labels)
                        or abs(sum(answer.probabilities.values()) - 1) > 0.01
                    ):
                        raise DecisionProviderError("response_invalid")
                results.append(result)
            with self._lock:
                self._failures = 0
                self._open_until = 0
            return DecisionResult(
                provider_id=self.provider_id,
                model=f"{self._model}@{self.model_revision}",
                answers={k: a for r in results for k, a in r.answers.items()},
                usage=DecisionUsage(
                    sum(r.usage.input_tokens for r in results), sum(r.usage.output_tokens for r in results), 0
                ),
                latency_ms=(self._clock() - started) * 1000,
                attempts=sum(r.attempts for r in results),
            )
        except DecisionProviderError as error:
            if error.retryable or error.reason_code in {"response_invalid", "deadline_exceeded"}:
                with self._lock:
                    self._failures += 1
                    if self._failures >= self._failure_limit:
                        self._open_until = self._clock() + self._reset_seconds
            raise
        finally:
            self._slots.release()
