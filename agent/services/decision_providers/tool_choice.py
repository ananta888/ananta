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
- a tool's text argument (e.g. ``query``): with a candidate source, one more
  choice over candidates (spans of the request, names from the symbol index,
  ``whole_request``, ``none_fits``) -- decision providers pick, they do not
  write; without one, or for ``whole_request``/``none_fits``, the request itself.

Confidence of a call is the lowest of the tool's and its arguments'
confidences; below ``min_confidence`` the result is an abstention, so the
normal (LLM) path decides.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.decision_providers.argument_candidates import NONE_FITS, WHOLE_REQUEST, ArgumentCandidateSource
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


def _text_question(index: int, tool: str, argument: str, prompt: str,
                   source: ArgumentCandidateSource) -> tuple[str, DecisionQuestion, dict[str, str]] | None:
    candidates = source.candidates(prompt, tool=tool, argument=argument)
    if not candidates:
        return None
    labels: dict[str, str] = {}
    for position, candidate in enumerate(candidates):
        label = _label(candidate.value, position)
        labels[label if label not in labels and label not in (WHOLE_REQUEST, NONE_FITS) else f"v{position}"] = \
            candidate.value
    options = {label: value for label, value in labels.items()}
    options[WHOLE_REQUEST] = "Use the complete request as the value."
    options[NONE_FITS] = "None of the candidates is right."
    key = ("text_" + _KEY.sub("_", f"{index}_{argument}".lower()))[:64]
    question = DecisionQuestion.choice(
        key, f"If the tool {tool} is used: which candidate is the best value for its '{argument}' argument "
        f"(the specific name or term to use)?", options)
    return key, question, labels


def tool_questions(tools: Sequence[Mapping[str, Any]], prompt: str, *, purpose: str = "tool_routing",
                   candidates: ArgumentCandidateSource | None = None,
                   ) -> tuple[ToolDecisionSchema, DecisionRequest, dict[str, Any]]:
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
    if candidates is not None:
        for index, text in enumerate(schema.text_arguments):
            built = _text_question(index, text.tool, text.argument, prompt, candidates)
            if built is not None:
                key, question, labels = built
                values[key] = (text, labels)
                questions.append(question)
    request = DecisionRequest(state=prompt, questions=tuple(questions), purpose=purpose)
    return schema, request, values


TEXT_MIN_CONFIDENCE = 0.5  # benchmark 2026-09-27: every wrongly chosen span was below 0.5


def read_tool_choice(schema: ToolDecisionSchema, result: DecisionResult, values: Mapping[str, Any], prompt: str, *,
                     min_confidence: float, text_min_confidence: float = TEXT_MIN_CONFIDENCE) -> ToolDecision:
    """The ``ToolDecision`` a decision result stands for.

    A text candidate is used only when chosen with at least ``text_min_confidence``; otherwise (and for
    ``whole_request``/``none_fits``) the request itself is the argument, as without candidates."""
    tool_answer = result.answer(TOOL_KEY)
    tool, confidence = tool_answer.choice, tool_answer.confidence
    if tool == NO_TOOL:
        if confidence < min_confidence:
            return ToolDecision(ABSTAIN, None, confidence=confidence, reason="decision_low_confidence")
        return ToolDecision(RESPOND, None, confidence=confidence, reason="decision_no_tool")
    arguments: dict[str, Any] = {}
    chosen_text: dict[str, str] = {}
    for key, (item, labels) in values.items():
        if item.tool != tool:
            continue
        answer = result.answer(key)
        if key.startswith("text_"):
            # a chosen candidate; whole_request / none_fits fall back to the request itself
            if answer.choice in labels and answer.confidence >= text_min_confidence:
                chosen_text[item.argument] = labels[answer.choice]
            continue
        arguments[item.argument] = labels[answer.choice]
        confidence = min(confidence, answer.confidence)
    text = next((t for t in schema.text_arguments if t.tool == tool), None)
    if text is not None:
        arguments[text.argument] = chosen_text.get(text.argument) or prompt.strip()[:TEXT_MAX_CHARS]
    elif tool in schema.free_text_tools:
        # a tool needing text we cannot place: never call it with a guess
        return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="decision_free_text_required")
    if confidence < min_confidence:
        return ToolDecision(ABSTAIN, tool, confidence=confidence, reason="decision_low_confidence")
    return ToolDecision(CALL, tool, arguments=arguments, confidence=confidence, reason="decision_call")
