"""Tool routing through llama.cpp's parallel-decision endpoint (Jev-style System 1).

The tiny-router adapter around ``ananta_contracts.tool_decision``, which builds
the decision schema and reads the response (shared with the Meet companion).
This module adds what is Hub-specific: the HTTP runtime (endpoint from the
profile's ``endpoint_env``), a circuit breaker, and the adapter contract.

The adapter only proposes a candidate to the existing tiny router: the
router's validator, risk filter and the policy gates behind it stay binding.
It abstains, so the normal (System 2) tool call runs, when the tool field is
uncertain, when ``none`` wins, when a chosen argument is uncertain, and when
the chosen tool needs free text this mode cannot produce. With profile
metadata ``open_field`` a tool's single required free string is generated in
the same call (see the contract module).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from agent.services.tiny_router.types import AdapterRequest, AdapterResult, TinyActionModelProfile
from ananta_contracts.tool_decision import (  # noqa: F401 -- re-exported for existing callers
    DECISION_PATH,
    NO_TOOL,
    TEXT_FIELD,
    TOOL_FIELD,
    ArgumentField,
    DecisionResponseError,
    TextArgument,
    ToolDecisionSchema,
    build_tool_decision_schema,
    fixed_values,
    read_tool_decision,
    request_body,
)

ADAPTER_ID = "parallel_decision"
ParallelDecisionResponseError = DecisionResponseError
decision_request_body = request_body


def decision_to_payload(response: Any, decision: ToolDecisionSchema, *, min_confidence: float) -> dict[str, Any]:
    """The tiny-router payload for one decision response."""
    return read_tool_decision(response, decision, min_confidence=min_confidence).router_payload()


class CircuitBreaker:
    """Open after ``threshold`` consecutive failures, for ``cooldown`` seconds."""

    def __init__(self, *, threshold: int = 3, cooldown: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._threshold = max(1, int(threshold))
        self._cooldown = float(cooldown)
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            return self._clock() >= self._open_until

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= self._threshold:
                self._open_until = self._clock() + self._cooldown
                self._failures = 0


class HttpParallelDecisionRuntime:
    """POSTs one decision to the endpoint named by the profile's ``endpoint_env``."""

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen,
                 environ: Mapping[str, str] | None = None) -> None:
        self._opener = opener
        self._environ = environ

    def base_url(self, profile: TinyActionModelProfile) -> str:
        env = os.environ if self._environ is None else self._environ
        name = str(profile.metadata.get("endpoint_env") or "")
        url = str(env.get(name) or "").strip().rstrip("/") if name else ""
        return url if url.startswith(("http://", "https://")) else ""

    def decide(self, profile: TinyActionModelProfile, body: Mapping[str, Any], *, timeout_ms: int) -> Any:
        request = urllib.request.Request(
            self.base_url(profile) + DECISION_PATH, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with self._opener(request, timeout=max(0.05, timeout_ms / 1000.0)) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            raise ParallelDecisionResponseError(f"parallel_decision_http_{int(error.code or 0)}") from None


class ParallelDecisionAdapter:
    """``TinyActionModelAdapter`` scoring the allowed tools with ``/v1/decision``."""

    adapter_id = ADAPTER_ID

    def __init__(self, runtime: Any | None = None, *, breaker: CircuitBreaker | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._runtime = runtime or HttpParallelDecisionRuntime()
        self._breaker = breaker or CircuitBreaker()
        self._clock = clock

    def is_available(self, profile: TinyActionModelProfile) -> tuple[bool, str]:
        if profile.adapter != self.adapter_id:
            return False, "adapter_profile_mismatch"
        base_url = getattr(self._runtime, "base_url", None)
        if callable(base_url) and not base_url(profile):
            return False, "parallel_decision_endpoint_unconfigured"
        if not self._breaker.allow():
            return False, "parallel_decision_circuit_open"
        return True, "parallel_decision_available"

    def propose(self, request: AdapterRequest) -> AdapterResult:
        started = self._clock()
        open_field = request.profile.metadata.get("open_field") is True
        decision = build_tool_decision_schema(request.tools, open_field=open_field)
        body = decision_request_body(decision, request.prompt, model_id=request.profile.model_id)
        try:
            response = self._runtime.decide(request.profile, body, timeout_ms=request.timeout_ms)
            payload = decision_to_payload(response, decision, min_confidence=request.profile.min_confidence)
        except Exception:
            self._breaker.record(False)
            raise
        self._breaker.record(True)
        return AdapterResult("candidate", payload, "adapter_completed", (self._clock() - started) * 1000.0)
