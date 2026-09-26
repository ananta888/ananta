"""Tool routing through llama.cpp's parallel-decision endpoint (Jev-style System 1).

Instead of letting a model write a tool call token by token, the allowed tools
become one typed decision: a ``tool`` field (every allowed tool plus ``none``)
and, per tool, every argument that has a fixed set of values (enum, boolean,
small integer or number grid). All fields are scored in one pass with a
probability each (``POST /v1/decision``).

The adapter only proposes a candidate to the existing tiny router: the
router's validator, risk filter and the policy gates behind it stay binding.
It abstains, so the normal (System 2) tool call runs, when the tool field is
uncertain, when ``none`` wins, when a chosen argument is uncertain, and when
the chosen tool needs a free-text argument this mode cannot produce.

Field dependencies (JEVCPP-005): ``tool`` is independent; each argument field
is grouped under its tool (scored as "if this tool were used") and only read
when that tool wins. Nothing else may depend on another field.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.services.tiny_router.types import AdapterRequest, AdapterResult, TinyActionModelProfile

ADAPTER_ID = "parallel_decision"
TOOL_FIELD = "tool"
NO_TOOL = "none"
MAX_VALUES = 255
MAX_FIELDS = 32
DECISION_PATH = "/v1/decision"
_FIELD_NAME = re.compile(r"[^A-Za-z0-9_]")
_INSTRUCTIONS = (
    "Decide how an agent should handle the user's request with the tools below. "
    "For the tool field choose the single best tool, or 'none' when no tool is needed. "
    "Every argument field assumes its tool is the one used."
)


class ParallelDecisionResponseError(ValueError):
    """The endpoint answered, but not with a decision this request can trust."""


# --- schema -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgumentField:
    name: str
    tool: str
    argument: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class ToolDecisionSchema:
    """The decision schema plus how its fields map back to tools and arguments."""

    schema: dict[str, Any]
    tools: tuple[str, ...]
    arguments: tuple[ArgumentField, ...]
    free_text_tools: frozenset[str]
    dependencies: Mapping[str, str] = field(default_factory=dict)

    def arguments_of(self, tool: str) -> list[ArgumentField]:
        return [item for item in self.arguments if item.tool == tool]


def _function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return function if isinstance(function, Mapping) else {}


def fixed_values(spec: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """The finite value set of a JSON-schema argument, or ``None`` when it is free."""
    if not isinstance(spec, Mapping):
        return None
    if isinstance(spec.get("enum"), list):
        values = spec["enum"]
        if 1 <= len(values) <= MAX_VALUES and all(isinstance(v, (str, int, float, bool)) for v in values):
            if len({json.dumps(v) for v in values}) == len(values):
                return tuple(values)
        return None
    kind = spec.get("type")
    if kind == "boolean":
        return (True, False)
    if kind == "integer" and isinstance(spec.get("minimum"), int) and isinstance(spec.get("maximum"), int):
        step = spec.get("multipleOf") if isinstance(spec.get("multipleOf"), int) and spec["multipleOf"] > 0 else 1
        values = tuple(range(spec["minimum"], spec["maximum"] + 1, step))
        return values if 1 <= len(values) <= MAX_VALUES else None
    return None


def _field_type(values: tuple[Any, ...]) -> dict[str, Any]:
    if values == (True, False):
        return {"type": "boolean"}
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values) and len(values) > 1:
        step = values[1] - values[0]
        if all(b - a == step for a, b in zip(values, values[1:])):
            return {"type": "integer", "minimum": values[0], "maximum": values[-1], "step": step}
    return {"type": "enum", "choices": [str(v) if not isinstance(v, str) else v for v in values]}


def build_tool_decision_schema(tools: Sequence[Mapping[str, Any]]) -> ToolDecisionSchema:
    names: list[str] = []
    arguments: list[ArgumentField] = []
    free_text: set[str] = set()
    descriptions: list[str] = []
    for tool in tools:
        function = _function(tool)
        name = str(function.get("name") or "").strip()
        if not name or name == NO_TOOL or name in names:
            continue
        names.append(name)
        descriptions.append(f"{name}: {str(function.get('description') or '').strip()[:200]}")
        parameters = function.get("parameters") if isinstance(function.get("parameters"), Mapping) else {}
        properties = parameters.get("properties") if isinstance(parameters.get("properties"), Mapping) else {}
        required = set(parameters.get("required") or [])
        for argument, spec in properties.items():
            values = fixed_values(spec)
            field_name = _FIELD_NAME.sub("_", f"{name}__{argument}")[:64]
            if values is None or len(arguments) + 1 >= MAX_FIELDS or not re.match(r"[A-Za-z_]", field_name):
                if argument in required:
                    free_text.add(name)
                continue
            arguments.append(ArgumentField(field_name, name, str(argument), values))
    if not names:
        raise ValueError("parallel_decision_no_tools")
    schema: dict[str, Any] = {
        TOOL_FIELD: {
            "type": "enum",
            "choices": [*names, NO_TOOL],
            "description": "Which tool should handle the request? " + " | ".join(descriptions)[:1500],
        }
    }
    for item in arguments:
        schema[item.name] = {
            **_field_type(item.values),
            "description": f"If {item.tool} is used: value of its argument '{item.argument}'.",
        }
    dependencies = {TOOL_FIELD: "independent", **{item.name: f"grouped:{item.tool}" for item in arguments}}
    return ToolDecisionSchema(schema, tuple(names), tuple(arguments), frozenset(free_text), dependencies)


# --- response -> router payload -----------------------------------------------------------------


def _probability(raw: Mapping[str, Any]) -> float:
    value = raw.get("probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ParallelDecisionResponseError("parallel_decision_probability_invalid")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise ParallelDecisionResponseError("parallel_decision_probability_invalid")
    return value


def _allowed(value: Any, values: tuple[Any, ...]) -> Any:
    """Map a scored value back to the argument's original value; unknown values are errors."""
    for original in values:
        if value == original or (not isinstance(original, str) and value == str(original)):
            return original
    raise ParallelDecisionResponseError("parallel_decision_value_not_allowed")


def decision_to_payload(response: Any, decision: ToolDecisionSchema, *, min_confidence: float) -> dict[str, Any]:
    """The tiny-router payload for one decision; strict about everything it reads."""
    if not isinstance(response, Mapping) or response.get("object") != "decision":
        raise ParallelDecisionResponseError("parallel_decision_response_invalid")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
        raise ParallelDecisionResponseError("parallel_decision_results_invalid")
    fields = results[0].get("fields")
    if not isinstance(fields, Mapping) or set(fields) != set(decision.schema):
        raise ParallelDecisionResponseError("parallel_decision_fields_mismatch")
    tool_field = fields[TOOL_FIELD]
    tool = tool_field.get("value") if isinstance(tool_field, Mapping) else None
    if tool not in (*decision.tools, NO_TOOL):
        raise ParallelDecisionResponseError("parallel_decision_tool_not_allowed")
    confidence = _probability(tool_field)
    if tool_field.get("abstain") is True or confidence < min_confidence:
        return {"tool_calls": [], "reason": "tool_uncertain", "confidence": confidence}
    if tool == NO_TOOL:
        return {"type": "respond", "reason": "no_tool_needed", "confidence": confidence}
    if tool in decision.free_text_tools:
        return {"tool_calls": [], "reason": "free_text_arguments", "tool_hint": tool, "confidence": confidence}
    arguments: dict[str, Any] = {}
    for item in decision.arguments_of(tool):
        raw = fields[item.name]
        if not isinstance(raw, Mapping):
            raise ParallelDecisionResponseError("parallel_decision_fields_mismatch")
        probability = _probability(raw)
        if raw.get("abstain") is True or probability < min_confidence:
            return {"tool_calls": [], "reason": "argument_uncertain", "tool_hint": tool, "confidence": probability}
        arguments[item.argument] = _allowed(raw.get("value"), item.values)
        confidence = min(confidence, probability)
    return {"tool_calls": [{"name": tool, "arguments": arguments, "confidence": confidence}]}


# --- runtime --------------------------------------------------------------------------------------


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
        decision = build_tool_decision_schema(request.tools)
        body = {
            "model": request.profile.model_id,
            "instructions": _INSTRUCTIONS,
            "schema": decision.schema,
            "contexts": [request.prompt],
            "mode": "tree",
            # A context is never reused across requests (tenant/request isolation);
            # the cached prefix is only the instructions and the schema.
            "cache_context": False,
        }
        try:
            response = self._runtime.decide(request.profile, body, timeout_ms=request.timeout_ms)
            payload = decision_to_payload(response, decision, min_confidence=request.profile.min_confidence)
        except Exception:
            self._breaker.record(False)
            raise
        self._breaker.record(True)
        return AdapterResult("candidate", payload, "adapter_completed", (self._clock() - started) * 1000.0)
