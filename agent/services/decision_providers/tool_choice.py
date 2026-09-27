"""Tool choice as typed decision questions (DPRV).

Translates the shared tool decision schema (``ananta_contracts.tool_decision``:
allowed tools, their required fixed-value arguments, their text argument) into
a ``DecisionRequest`` any decision provider can answer, and the answer back
into the same ``ToolDecision`` the local ``/v1/decision`` path produces. The
tiny router adapter and the benchmark both use this module, so what is
measured is what runs.

- ``tool``: a choice over the allowed tools plus ``none`` (answer without a tool);
- every required fixed-value argument: a choice over its values, asked in the
  same request (typed decision APIs evaluate questions in parallel);
- a tool's text argument (e.g. ``query``) gets the user's request itself:
  decision providers pick, they do not write.

Confidence of a call is the lowest of the tool's and its arguments'
confidences; below ``min_confidence`` the result is an abstention, so the
normal (LLM) path decides.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.decision_providers.types import DecisionQuestion, DecisionRequest, DecisionResult
from ananta_contracts.tool_decision import (
    ABSTAIN,
    CALL,
    NO_TOOL,
    RESPOND,
    ToolDecision,
    ToolDecisionSchema,
    build_tool_decision_schema,
)

TOOL_KEY = "tool"
TEXT_MAX_CHARS = 1000
_KEY = re.compile(r"[^a-z0-9_]")


def _function(tool: Mapping[str, Any]) -> Mapping[str, Any]:
    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    return function if isinstance(function, Mapping) else {}


def _label(value: Any, index: int) -> str:
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}", text) else f"v{index}"


def argument_key(index: int, argument: str) -> str:
    return ("arg_" + _KEY.sub("_", f"{index}_{argument}".lower()))[:64]


def tool_questions(tools: Sequence[Mapping[str, Any]], prompt: str, *,
                   purpose: str = "tool_routing") -> tuple[ToolDecisionSchema, DecisionRequest, dict[str, Any]]:
    """``(schema, request, value maps)`` for choosing among ``tools`` for ``prompt``."""
    schema = build_tool_decision_schema(tools, open_field=True)  # knows each tool's text argument
    descriptions = {str(_function(t).get("name") or ""): str(_function(t).get("description") or "").strip()
                    for t in tools}
    options = {name: (descriptions.get(name) or name)[:400] for name in schema.tools}
    options[NO_TOOL] = "No tool is needed: answer directly (small talk, general knowledge, or nothing to look up)."
    questions = [DecisionQuestion.choice(TOOL_KEY, "Which tool should handle the user's request?", options)]
    values: dict[str, Any] = {}
    for index, item in enumerate(schema.arguments):
        key = argument_key(index, item.argument)
        labels = {_label(value, position): value for position, value in enumerate(item.values)}
        values[key] = (item, labels)
        questions.append(DecisionQuestion.choice(
            key, f"If the tool {item.tool} is used: which value fits its argument '{item.argument}'?",
            {label: str(value) for label, value in labels.items()}))
    request = DecisionRequest(state=prompt, questions=tuple(questions), purpose=purpose)
    return schema, request, values


def read_tool_choice(schema: ToolDecisionSchema, result: DecisionResult, values: Mapping[str, Any], prompt: str, *,
                     min_confidence: float) -> ToolDecision:
    """The ``ToolDecision`` a decision result stands for."""
    tool_answer = result.answer(TOOL_KEY)
    tool, confidence = tool_answer.choice, tool_answer.confidence
    if tool == NO_TOOL:
        if confidence < min_confidence:
            return ToolDecision(ABSTAIN, None, confidence=confidence, reason="decision_low_confidence")
        return ToolDecision(RESPOND, None, confidence=confidence, reason="decision_no_tool")
    arguments: dict[str, Any] = {}
    for key, (item, labels) in values.items():
        if item.tool != tool:
            continue
        answer = result.answer(key)
        arguments[item.argument] = labels[answer.choice]
        confidence = min(confidence, answer.confidence)
    text = next((t for t in schema.text_arguments if t.tool == tool), None)
    if text is not None:
        arguments[text.argument] = prompt.strip()[:TEXT_MAX_CHARS]
    elif tool in schema.free_text_tools:
        # a tool needing text we cannot place: never call it with a guess
        return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="decision_free_text_required")
    if confidence < min_confidence:
        return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="decision_low_confidence")
    return ToolDecision(CALL, tool, arguments=arguments, confidence=confidence, reason="decision_call")
