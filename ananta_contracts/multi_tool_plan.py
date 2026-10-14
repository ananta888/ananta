"""Multi-tool planning as one typed decision (PDT, backend-neutral).

The single-tool contract lives in ``ananta_contracts.tool_decision``: one
``tool`` choice plus that tool's argument fields. This module generalises it to
plan *several* independent tool calls in one ``POST /v1/decision`` request:

- one ``<tool>__enabled`` boolean per tool, scored with a probability, so a
  tool is selected on its own and not against the others;
- the finite (closed) required arguments of every tool as their own fields;
- free-text and otherwise unrepresentable required arguments are recorded, not
  guessed: the caller falls back to System 2 or a targeted generation;
- per-step confidence, abstain, missing arguments and errors;
- a deterministic, schema-bound assembly order and optional validation of the
  assembled arguments against registered tool schemas.

Like ``tool_decision`` this module owns no transport and no policy: an adapter
posts the body with its own client, and the Hub keeps every gate. A plan is a
proposal; ``confidence`` is never authorisation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.tool_decision import fixed_values

PLAN_SCHEMA = "ananta.multi_tool_plan.v1"
ENABLED_SUFFIX = "__enabled"
TEXT_FIELD = "text_argument"
DECISION_PATH = "/v1/decision"

MAX_TOOLS = 32
MAX_FIELDS = 32
TEXT_MAX_CHARS = 200
DEFAULT_MAX_CALLS = 4

INSTRUCTIONS = (
    "Decide which of the tools below, if any, an agent should call. Score each tool's "
    "'enabled' field independently: several may be enabled at once when the request needs "
    "them, and all may stay false when no tool is needed. Every argument field assumes its "
    "own tool is the one used."
)

CALL = "call"
ABSTAIN = "abstain"
SKIPPED = "skipped"
ERROR = "error"

PLAN = "plan"
PARTIAL = "partial"
RESPOND = "respond"
OVER_BUDGET = "over_budget"

_FIELD_NAME = re.compile(r"[^A-Za-z0-9_]")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PlanResponseError(ValueError):
    """The endpoint answered, but not with a plan the request can trust."""


class PlanSchemaError(ValueError):
    """The tool definitions cannot be turned into a decision schema."""


# --- schema -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanArgument:
    """A finite (closed) argument of a tool, scored as its own field."""

    field_name: str
    tool: str
    argument: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class PlanTextField:
    """A free-text or unrepresentable required argument the model cannot score."""

    tool: str
    argument: str
    required: bool = True


@dataclass(frozen=True)
class MultiToolPlanSchema:
    """The ``/v1/decision`` schema plus how its fields map back to tools."""

    schema: dict[str, Any]
    tools: tuple[str, ...]
    enabled_fields: Mapping[str, str]
    arguments: tuple[PlanArgument, ...]
    text_fields: tuple[PlanTextField, ...]
    free_text_tools: frozenset[str]
    dependencies: Mapping[str, Any]
    max_calls: int = DEFAULT_MAX_CALLS
    open_field_tool: str | None = None

    def enabled_field(self, tool: str) -> str:
        return self.enabled_fields[tool]

    def arguments_of(self, tool: str) -> tuple[PlanArgument, ...]:
        return tuple(item for item in self.arguments if item.tool == tool)

    def text_fields_of(self, tool: str) -> tuple[PlanTextField, ...]:
        return tuple(item for item in self.text_fields if item.tool == tool)


def _function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return function if isinstance(function, Mapping) else {}


def _is_free_string(spec: Any) -> bool:
    return isinstance(spec, Mapping) and spec.get("type") == "string" and "enum" not in spec


def _field_type(values: tuple[Any, ...]) -> dict[str, Any]:
    if values == (True, False) and all(type(value) is bool for value in values):
        return {"type": "boolean"}
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values) and len(values) > 1:
        step = values[1] - values[0]
        if step > 0 and all(b - a == step for a, b in zip(values, values[1:])):
            return {"type": "integer", "minimum": values[0], "maximum": values[-1], "step": step}
    return {"type": "enum", "choices": [str(v) if not isinstance(v, str) else v for v in values]}


def _field_name(tool: str, argument: str) -> str:
    return _FIELD_NAME.sub("_", f"{tool}__{argument}")[:64]


def _enabled_name(tool: str) -> str:
    return _FIELD_NAME.sub("_", tool + ENABLED_SUFFIX)[:64]


def _valid_tool_name(name: str) -> bool:
    return bool(_KEY.fullmatch(_FIELD_NAME.sub("_", name.lower())[:64]))


def build_multi_tool_plan_schema(
    tools: Sequence[Mapping[str, Any]],
    *,
    max_calls: int = DEFAULT_MAX_CALLS,
    open_field: bool = False,
) -> MultiToolPlanSchema:
    """Turn OpenAI-style tool definitions into one multi-field decision schema.

    Raises ``PlanSchemaError`` when no usable tool remains or two tools collapse
    onto the same field name. Tools whose required arguments cannot be scored at
    all are recorded in ``free_text_tools`` so the caller routes them to System 2
    instead of guessing.
    """
    names: list[str] = []
    descriptions: dict[str, str] = {}
    arguments: list[PlanArgument] = []
    text_fields: list[PlanTextField] = []
    enabled_fields: dict[str, str] = {}
    free_text_tools: set[str] = set()
    used_fields: set[str] = set()
    budget = MAX_FIELDS - (1 if open_field else 0)
    for tool in tools:
        function = _function(tool)
        name = str(function.get("name") or "").strip()
        if not name or name in names or not _valid_tool_name(name):
            continue
        enabled_name = _enabled_name(name)
        if enabled_name in used_fields:
            raise PlanSchemaError("plan_field_collision")
        if len(names) >= MAX_TOOLS or len(used_fields) + 1 > budget:
            raise PlanSchemaError("plan_field_budget_exceeded")
        pending: list[PlanArgument] = []
        texts: list[PlanTextField] = []
        parameters = function.get("parameters") if isinstance(function.get("parameters"), Mapping) else {}
        unresolvable = bool(
            set(parameters)
            & {"if", "then", "else", "dependentRequired", "dependentSchemas", "oneOf", "anyOf", "allOf", "$ref"}
        )
        properties = parameters.get("properties") if isinstance(parameters.get("properties"), Mapping) else {}
        for argument in (str(item) for item in (parameters.get("required") or []) if str(item)):
            spec = properties.get(argument)
            field_name = _field_name(name, argument)
            values = fixed_values(spec)
            if (
                field_name in used_fields
                or field_name == enabled_name
                or any(item.field_name == field_name for item in pending)
            ):
                raise PlanSchemaError("plan_field_collision")
            if values is not None and len(used_fields) + 1 + len(pending) < budget:
                pending.append(PlanArgument(field_name, name, argument, values))
            elif _is_free_string(spec):
                texts.append(PlanTextField(name, argument))
            else:
                unresolvable = True
                break
        names.append(name)
        descriptions[name] = str(function.get("description") or "")
        enabled_fields[name] = enabled_name
        used_fields.add(enabled_name)
        if unresolvable:
            # the tool still gets an enabled field, but no argument can be scored:
            # a decision can enable it, the caller routes it to System 2
            free_text_tools.add(name)
            continue
        arguments.extend(pending)
        used_fields.update(item.field_name for item in pending)
        text_fields.extend(texts)
    if not names:
        raise PlanSchemaError("plan_no_tools")
    schema: dict[str, Any] = {}
    for name in names:
        schema[enabled_fields[name]] = {
            "type": "boolean",
            "description": f"Should {name} be called? {descriptions[name]} Independent of other tools.",
        }
    for item in arguments:
        schema[item.field_name] = {
            **_field_type(item.values),
            "description": f"If {item.tool} is called: value of its argument '{item.argument}'.",
        }
    open_tool: str | None = None
    if open_field and len(text_fields) == 1 and text_fields[0].tool not in free_text_tools:
        open_tool = text_fields[0].tool
        schema[TEXT_FIELD] = {
            "type": "string",
            "max_tokens": 48,
            "when": {enabled_fields[open_tool]: [True]},
            "description": (
                f"The value of {open_tool}'s '{text_fields[0].argument}' argument. "
                "An empty string when it needs no text."
            ),
        }
    dependencies: dict[str, Any] = {enabled_fields[name]: "independent" for name in names}
    dependencies.update({item.field_name: f"grouped:{item.tool}" for item in arguments})
    if open_tool is not None:
        dependencies[TEXT_FIELD] = f"conditional:{enabled_fields[open_tool]}"
    return MultiToolPlanSchema(
        schema=schema,
        tools=tuple(names),
        enabled_fields=enabled_fields,
        arguments=tuple(arguments),
        text_fields=tuple(text_fields),
        free_text_tools=frozenset(free_text_tools),
        dependencies=dependencies,
        max_calls=max(1, int(max_calls)),
        open_field_tool=open_tool,
    )


def plan_request_body(schema: MultiToolPlanSchema, prompt: str, *, model_id: str = "") -> dict[str, Any]:
    """The ``/v1/decision`` request body for one prompt (one context shared by all fields)."""
    return {
        "model": model_id,
        "instructions": INSTRUCTIONS,
        "schema": schema.schema,
        "contexts": [prompt],
        "mode": "tree",
        "cache_context": False,
        "cache_prompt": False,
        "cache_media": False,
    }


# --- response -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolPlanStep:
    tool: str
    status: str
    reason: str
    enabled: bool = False
    arguments: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    generated_arguments: tuple[str, ...] = ()
    missing_arguments: tuple[str, ...] = ()

    @property
    def call(self) -> bool:
        return self.status == CALL

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "reason": self.reason,
            "enabled": self.enabled,
            "arguments": dict(self.arguments),
            "confidence": round(self.confidence, 4),
            "generated_arguments": list(self.generated_arguments),
            "missing_arguments": list(self.missing_arguments),
        }


@dataclass(frozen=True)
class MultiToolPlan:
    """The plan in one response: which tools to call, in schema order, and why."""

    status: str
    reason: str
    steps: tuple[ToolPlanStep, ...]
    schema_version: str = PLAN_SCHEMA

    def calls(self) -> tuple[ToolPlanStep, ...]:
        return tuple(step for step in self.steps if step.call)

    def to_mapping(self, *, include_arguments: bool = True) -> dict[str, Any]:
        return {
            "schema": self.schema_version,
            "status": self.status,
            "reason": self.reason,
            "steps": [
                step.as_dict() if include_arguments else {**step.as_dict(), "arguments": {}} for step in self.steps
            ],
        }

    def router_payload(self) -> dict[str, Any]:
        """The tiny-router shaped payload: the enabled calls, in schema order."""
        calls = self.calls()
        if not calls:
            return {"tool_calls": [], "status": self.status, "reason": self.reason}
        payload: dict[str, Any] = {
            "tool_calls": [
                {"name": step.tool, "arguments": dict(step.arguments), "confidence": step.confidence} for step in calls
            ],
            "status": self.status,
            "reason": self.reason,
        }
        generated = sorted({name for step in calls for name in step.generated_arguments})
        if generated:
            payload["generated_arguments"] = generated
        return payload


def _probability(raw: Any) -> float:
    value = raw.get("probability") if isinstance(raw, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PlanResponseError("plan_probability_invalid")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise PlanResponseError("plan_probability_invalid")
    return value


def _allowed(value: Any, values: tuple[Any, ...]) -> Any:
    for original in values:
        if (type(value) is type(original) and value == original) or (
            not isinstance(original, str) and isinstance(value, str) and value == str(original)
        ):
            return original
    raise PlanResponseError("plan_value_not_allowed")


def _bool_value(raw: Any, field_name: str) -> bool:
    value = raw.get("value") if isinstance(raw, Mapping) else None
    if not isinstance(value, bool):
        raise PlanResponseError(f"plan_enabled_value_invalid:{field_name}")
    return value


def _open_text(raw: Any) -> str | None:
    """The generated open field's text ("" when empty) when complete and usable; ``None`` otherwise."""
    if isinstance(raw, Mapping) and raw.get("skipped") is True:
        return None
    if not isinstance(raw, Mapping) or raw.get("generated") is not True:
        raise PlanResponseError("plan_open_field_invalid")
    value = raw.get("value")
    if raw.get("skipped") is True or not isinstance(value, str) or raw.get("truncated") is not False:
        return None
    text = value.strip()
    if len(text) > TEXT_MAX_CHARS or _CONTROL.search(text):
        return None
    return text


def _plan_status(steps: Sequence[ToolPlanStep], *, over_budget: bool) -> tuple[str, str]:
    calls = [step for step in steps if step.call]
    if over_budget:
        return OVER_BUDGET, "plan_max_calls_exceeded"
    if any(step.status == ERROR for step in steps):
        return ERROR, "plan_step_error"
    undecided = [step for step in steps if step.status in {ABSTAIN, ERROR}]
    if calls and undecided:
        return PARTIAL, "plan_partial"
    if calls:
        return PLAN, "plan_ready"
    if undecided:
        return ABSTAIN, "plan_no_call"
    return RESPOND, "plan_no_tool_needed"


def read_multi_tool_plan(
    response: Any,
    schema: MultiToolPlanSchema,
    *,
    min_confidence: float,
    max_calls: int | None = None,
    argument_validator: Callable[[str, Mapping[str, Any]], Sequence[str]] | None = None,
) -> MultiToolPlan:
    """The plan in one decision response; strict about structure, conservative about calls.

    ``argument_validator`` is an injected schema check (DIP): it returns a list of
    issues for ``(tool, arguments)``. The Hub passes its registry-backed validator;
    a tool whose arguments fail is a step ``error``, never a call.
    """
    if type(min_confidence) not in {int, float} or not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise PlanResponseError("plan_confidence_threshold_invalid")
    if not isinstance(response, Mapping) or response.get("object") != "decision":
        raise PlanResponseError("plan_response_invalid")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
        raise PlanResponseError("plan_results_invalid")
    fields = results[0].get("fields")
    if not isinstance(fields, Mapping) or set(fields) != set(schema.schema):
        raise PlanResponseError("plan_fields_mismatch")
    limit = schema.max_calls if max_calls is None else max(1, int(max_calls))
    generated_text = _open_text(fields[TEXT_FIELD]) if TEXT_FIELD in schema.schema else None
    steps: list[ToolPlanStep] = []
    for tool in schema.tools:
        enabled_field = schema.enabled_fields[tool]
        raw_enabled = fields[enabled_field]
        enabled = _bool_value(raw_enabled, enabled_field)
        enabled_confidence = _probability(raw_enabled)
        if raw_enabled.get("abstain") is True or enabled_confidence < min_confidence:
            steps.append(ToolPlanStep(tool, ABSTAIN, "enabled_uncertain", confidence=enabled_confidence))
            continue
        if not enabled:
            steps.append(ToolPlanStep(tool, SKIPPED, "not_selected", confidence=enabled_confidence))
            continue
        if tool in schema.free_text_tools:
            missing = tuple(item.argument for item in schema.text_fields_of(tool))
            steps.append(
                ToolPlanStep(
                    tool,
                    ABSTAIN,
                    "free_text_arguments",
                    enabled=True,
                    confidence=enabled_confidence,
                    missing_arguments=missing,
                )
            )
            continue
        arguments: dict[str, Any] = {}
        confidence = enabled_confidence
        text_fields = schema.text_fields_of(tool)
        if text_fields:
            argument = text_fields[0].argument
            if schema.open_field_tool == tool and generated_text:
                arguments[argument] = generated_text
            elif text_fields[0].required:
                steps.append(
                    ToolPlanStep(
                        tool,
                        ABSTAIN,
                        "free_text_required",
                        enabled=True,
                        confidence=confidence,
                        missing_arguments=(argument,),
                    )
                )
                continue
        uncertain = False
        for item in schema.arguments_of(tool):
            raw = fields[item.field_name]
            if not isinstance(raw, Mapping):
                raise PlanResponseError("plan_fields_mismatch")
            probability = _probability(raw)
            if raw.get("abstain") is True or probability < min_confidence:
                uncertain = True
                break
            arguments[item.argument] = _allowed(raw.get("value"), item.values)
            confidence = min(confidence, probability)
        if uncertain:
            steps.append(
                ToolPlanStep(
                    tool, ABSTAIN, "argument_uncertain", enabled=True, confidence=confidence, arguments=arguments
                )
            )
            continue
        if argument_validator is not None:
            issues = tuple(str(issue) for issue in argument_validator(tool, arguments))
            if issues:
                steps.append(
                    ToolPlanStep(
                        tool,
                        ERROR,
                        "arguments_failed_schema",
                        enabled=True,
                        confidence=confidence,
                        arguments=arguments,
                        missing_arguments=issues,
                    )
                )
                continue
        generated = tuple(item.argument for item in text_fields if item.argument in arguments)
        steps.append(
            ToolPlanStep(
                tool,
                CALL,
                "plan_call",
                enabled=True,
                arguments=arguments,
                confidence=confidence,
                generated_arguments=generated,
            )
        )
    status, reason = _plan_status(steps, over_budget=len([s for s in steps if s.call]) > limit)
    return MultiToolPlan(status=status, reason=reason, steps=tuple(steps))


def plan_from_mapping(row: Mapping[str, Any]) -> MultiToolPlan:
    """Rebuild a plan from ``MultiToolPlan.to_mapping`` output (round-trip for audit and tests)."""
    if not isinstance(row, Mapping) or row.get("schema") != PLAN_SCHEMA:
        raise PlanResponseError("plan_mapping_invalid")
    steps = row.get("steps")
    if not isinstance(steps, list):
        raise PlanResponseError("plan_mapping_invalid")
    return MultiToolPlan(
        status=str(row.get("status") or ""),
        reason=str(row.get("reason") or ""),
        steps=tuple(
            ToolPlanStep(
                tool=str(step.get("tool") or ""),
                status=str(step.get("status") or ""),
                reason=str(step.get("reason") or ""),
                enabled=bool(step.get("enabled", False)),
                arguments=dict(step.get("arguments") or {}),
                confidence=float(step.get("confidence") or 0.0),
                generated_arguments=tuple(str(item) for item in (step.get("generated_arguments") or [])),
                missing_arguments=tuple(str(item) for item in (step.get("missing_arguments") or [])),
            )
            for step in steps
            if isinstance(step, Mapping)
        ),
    )


def plan_fingerprint(plan: MultiToolPlan) -> str:
    """A deterministic digest of the plan's calls and arguments (ignores confidence and disabled steps)."""
    calls = [{"name": step.tool, "arguments": step.arguments} for step in plan.calls()]
    payload = json.dumps(calls, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
