"""Tool choice as one typed decision for llama.cpp's ``POST /v1/decision`` (Jev-style System 1).

Shared by the Hub's tiny-router adapter and the Meet companion, which cannot
import the Hub package: builds the decision schema from OpenAI tool
definitions, the request body, and reads a response strictly into a
``ToolDecision``. No transport and no policy here: callers post the body with
their own client and keep their own gates.

Schema: a ``tool`` field (every tool plus ``none``); per tool, every required
argument with a fixed value set (enum, boolean, small integer range) as a
field scored "as if this tool were used" (optional ones keep the tool's
default: nobody asked for them, and an unsure pick would drag the call's
confidence down); and, with ``open_field``, one bounded open field that
fills a tool's text argument: its single required free string, or, for a
tool that declares nothing as required (the Hub's MCP tools), its leading
parameter when that is a free string (a search ``query``, a grep
``pattern``, a ``handle``); an empty value then means "without it". A tool
whose leading parameter is something else cannot be filled this way and is
left to the model; a tool without parameters is called as it is. The open
field is generated after the closed fields, only when a tool that takes it
wins (``when``), so it is conditioned on the chosen tool.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

TOOL_FIELD = "tool"
TEXT_FIELD = "text_argument"
NO_TOOL = "none"
MAX_VALUES = 255
MAX_FIELDS = 32
TEXT_MAX_TOKENS = 48
TEXT_MAX_CHARS = 200
DECISION_PATH = "/v1/decision"
INSTRUCTIONS = (
    "Decide how an agent should handle the user's request with the tools below. "
    "For the tool field choose the single best tool, or 'none' when no tool is needed. "
    "Every argument field assumes its tool is the one used."
)
_FIELD_NAME = re.compile(r"[^A-Za-z0-9_]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

CALL = "call"
RESPOND = "respond"
ABSTAIN = "abstain"


class DecisionResponseError(ValueError):
    """The endpoint answered, but not with a decision the request can trust."""


# --- schema -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArgumentField:
    name: str
    tool: str
    argument: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class TextArgument:
    """A tool's text argument, filled from the open field; an optional one may stay empty."""

    tool: str
    argument: str
    required: bool = True


@dataclass(frozen=True)
class ToolDecisionSchema:
    """The decision schema plus how its fields map back to tools and arguments."""

    schema: dict[str, Any]
    tools: tuple[str, ...]
    arguments: tuple[ArgumentField, ...]
    free_text_tools: frozenset[str]
    dependencies: Mapping[str, str] = field(default_factory=dict)
    text_arguments: tuple[TextArgument, ...] = ()

    def arguments_of(self, tool: str) -> list[ArgumentField]:
        return [item for item in self.arguments if item.tool == tool]

    def text_argument_of(self, tool: str) -> TextArgument | None:
        return next((item for item in self.text_arguments if item.tool == tool), None)


def _function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return function if isinstance(function, Mapping) else {}


def fixed_values(spec: Any) -> tuple[Any, ...] | None:
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


def _is_free_string(spec: Any) -> bool:
    return isinstance(spec, Mapping) and spec.get("type") == "string" and "enum" not in spec


def _text_argument(tool: str, properties: Mapping[str, Any], required: set, missing: list[str]) -> TextArgument | None:
    """The argument the open field fills: the single required free string, else a free leading parameter."""
    if missing:
        if len(missing) == 1 and _is_free_string(properties.get(missing[0])):
            return TextArgument(tool, missing[0])
        return None
    if not required and properties:
        leading = next(iter(properties))
        if _is_free_string(properties[leading]):
            return TextArgument(tool, str(leading), required=False)
    return None


def _needs_text(properties: Mapping[str, Any], required: set) -> bool:
    """A tool without required arguments whose leading parameter is free: calling it empty would be a guess."""
    if required or not properties:
        return False
    return fixed_values(properties[next(iter(properties))]) is None


def _field_type(values: tuple[Any, ...]) -> dict[str, Any]:
    if values == (True, False):
        return {"type": "boolean"}
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values) and len(values) > 1:
        step = values[1] - values[0]
        if all(b - a == step for a, b in zip(values, values[1:])):
            return {"type": "integer", "minimum": values[0], "maximum": values[-1], "step": step}
    return {"type": "enum", "choices": [str(v) if not isinstance(v, str) else v for v in values]}


def build_tool_decision_schema(tools: Sequence[Mapping[str, Any]], *, open_field: bool = False) -> ToolDecisionSchema:
    """Tool choice as one decision; with ``open_field`` a single free string argument per tool is generated."""
    names: list[str] = []
    arguments: list[ArgumentField] = []
    free_text: set[str] = set()
    texts: list[TextArgument] = []
    text_notes: list[str] = []
    descriptions: list[str] = []
    budget = MAX_FIELDS - 1 - (1 if open_field else 0)  # the tool field and the open field
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
        missing: list[str] = []
        for argument, spec in properties.items():
            if argument not in required:
                continue  # optional: the tool's default applies
            values = fixed_values(spec)
            field_name = _FIELD_NAME.sub("_", f"{name}__{argument}")[:64]
            if values is None or len(arguments) >= budget or not re.match(r"[A-Za-z_]", field_name):
                missing.append(str(argument))
                continue
            arguments.append(ArgumentField(field_name, name, str(argument), values))
        missing.extend(str(argument) for argument in required if argument not in properties)
        text = _text_argument(name, properties, required, missing) if open_field else None
        if text is not None:
            texts.append(text)
            note = str((properties[text.argument] or {}).get("description") or "").strip()[:100]
            optional = "" if text.required else ", optional"
            text_notes.append(f"{name}: its '{text.argument}'{optional}" + (f" ({note})" if note else ""))
        elif missing or _needs_text(properties, required):
            free_text.add(name)
    if not names:
        raise ValueError("tool_decision_no_tools")
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
    if texts:
        # generated last, after the chosen closed values, and only for a tool that takes the text
        schema[TEXT_FIELD] = {
            "type": "string",
            "max_tokens": TEXT_MAX_TOKENS,
            "when": {TOOL_FIELD: [item.tool for item in texts]},
            "description": ("The text argument of the chosen tool. " + "; ".join(text_notes))[:1200]
            + ". An empty string when the chosen tool needs no text.",
        }
        dependencies[TEXT_FIELD] = "conditional:closed_fields"
    return ToolDecisionSchema(schema, tuple(names), tuple(arguments), frozenset(free_text), dependencies, tuple(texts))


def request_body(decision: ToolDecisionSchema, prompt: str, *, model_id: str = "") -> dict[str, Any]:
    """The ``/v1/decision`` request for one prompt."""
    return {
        "model": model_id,
        "instructions": INSTRUCTIONS,
        "schema": decision.schema,
        "contexts": [prompt],
        "mode": "tree",
        # A context is never reused across requests (tenant/request isolation);
        # the cached prefix is only the instructions and the schema.
        "cache_context": False,
    }


# --- response -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDecision:
    """What the decision says: call a tool, answer without one, or abstain (the normal path decides)."""

    status: str
    tool: str | None = None  # the tool to call, or the likely tool of an abstention
    arguments: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    generated_arguments: tuple[str, ...] = ()  # generated, not scored: no probability of their own
    margin: float | None = None

    def router_payload(self) -> dict[str, Any]:
        """The tiny-router adapter payload for this decision."""
        if self.status == CALL:
            payload: dict[str, Any] = {"tool_calls": [{"name": self.tool, "arguments": dict(self.arguments),
                                                       "confidence": self.confidence}]}
            if self.generated_arguments:
                payload["generated_arguments"] = list(self.generated_arguments)
            return payload
        if self.status == RESPOND:
            return {"type": "respond", "reason": self.reason, "confidence": self.confidence}
        payload = {"tool_calls": [], "reason": self.reason, "confidence": self.confidence}
        if self.tool:
            payload["tool_hint"] = self.tool
        return payload


def _probability(raw: Mapping[str, Any]) -> float:
    value = raw.get("probability")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise DecisionResponseError("tool_decision_probability_invalid")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise DecisionResponseError("tool_decision_probability_invalid")
    return value


def _margin(raw: Mapping[str, Any]) -> float | None:
    value = raw.get("margin")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _allowed(value: Any, values: tuple[Any, ...]) -> Any:
    """Map a scored value back to the argument's original value; unknown values are errors."""
    for original in values:
        if value == original or (not isinstance(original, str) and value == str(original)):
            return original
    raise DecisionResponseError("tool_decision_value_not_allowed")


def _open_text(raw: Any) -> str | None:
    """The open field's text ("" when empty) when it is complete and usable; ``None`` otherwise."""
    if not isinstance(raw, Mapping) or raw.get("generated") is not True:
        raise DecisionResponseError("tool_decision_open_field_invalid")
    value = raw.get("value")
    if raw.get("skipped") is True or not isinstance(value, str) or raw.get("truncated") is not False:
        return None
    text = value.strip()
    if len(text) > TEXT_MAX_CHARS or _CONTROL.search(text):
        return None
    return text


def read_tool_decision(response: Any, decision: ToolDecisionSchema, *, min_confidence: float) -> ToolDecision:
    """The decision in one response; strict about everything it reads, conservative about everything it accepts."""
    if not isinstance(response, Mapping) or response.get("object") != "decision":
        raise DecisionResponseError("tool_decision_response_invalid")
    results = response.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], Mapping):
        raise DecisionResponseError("tool_decision_results_invalid")
    fields = results[0].get("fields")
    if not isinstance(fields, Mapping) or set(fields) != set(decision.schema):
        raise DecisionResponseError("tool_decision_fields_mismatch")
    tool_field = fields[TOOL_FIELD]
    tool = tool_field.get("value") if isinstance(tool_field, Mapping) else None
    if tool not in (*decision.tools, NO_TOOL):
        raise DecisionResponseError("tool_decision_tool_not_allowed")
    confidence = _probability(tool_field)
    margin = _margin(tool_field)
    if tool_field.get("abstain") is True or confidence < min_confidence:
        return ToolDecision(ABSTAIN, tool if tool != NO_TOOL else None, confidence=confidence,
                            reason="tool_uncertain", margin=margin)
    if tool == NO_TOOL:
        return ToolDecision(RESPOND, confidence=confidence, reason="no_tool_needed", margin=margin)
    if tool in decision.free_text_tools:
        return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="free_text_arguments", margin=margin)
    arguments: dict[str, Any] = {}
    generated: tuple[str, ...] = ()
    text_argument = decision.text_argument_of(tool)
    if text_argument is not None:
        text = _open_text(fields[TEXT_FIELD])
        if text is None or (text == "" and text_argument.required):
            return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="text_argument_invalid", margin=margin)
        if text:
            arguments[text_argument.argument] = text
            generated = (text_argument.argument,)
    for item in decision.arguments_of(tool):
        raw = fields[item.name]
        if not isinstance(raw, Mapping):
            raise DecisionResponseError("tool_decision_fields_mismatch")
        probability = _probability(raw)
        if raw.get("abstain") is True or probability < min_confidence:
            return ToolDecision(ABSTAIN, tool, confidence=probability, reason="argument_uncertain", margin=margin)
        arguments[item.argument] = _allowed(raw.get("value"), item.values)
        confidence = min(confidence, probability)
    return ToolDecision(CALL, tool, arguments, confidence, generated_arguments=generated, margin=margin)
