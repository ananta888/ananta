"""The result of a long-context step as the next step or the original task needs it (LCTX-010).

In the autopilot's single-shot flow an analysis step hands its answer over in the proposal:
as the ``content`` of a ``file_write`` or as the text fields of a result call (``summary``,
``answer``, ...). ``last_output`` then only reports "file written". The Hub keeps the
proposal, so the answer is read from there -- no shared workspace between containers --
and ``last_output`` stays the fallback when the proposal carries no text.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from typing import Any

TEXT_FIELDS = ("content", "answer", "result", "summary", "text", "details", "findings", "points")
# the execution report of a final_answer call: "Tool 'final_answer': Erfolg\nOutput: {'answer': '...'}"
_FINAL_ANSWER_OUTPUT = re.compile(r"Tool 'final_answer': [^\n]*\nOutput: (\{.*\})\s*$", re.DOTALL)


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {_render(item)}" for item in value if _render(item))
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, indent=1)
    return "" if value is None else str(value)


def _proposal(task: Any) -> dict[str, Any]:
    raw = getattr(task, "last_proposal", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _calls_text(tool_calls: Any) -> str:
    parts: list[str] = []
    for call in tool_calls or []:
        if not isinstance(call, Mapping):
            continue
        args = call.get("args") or call.get("arguments") or {}
        if not isinstance(args, Mapping):
            continue
        for field in TEXT_FIELDS:
            text = _render(args.get(field))
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def proposal_text(task: Any) -> str:
    """The text the step's proposal carried in its tool-call arguments, or ``""``."""
    return _calls_text(_proposal(task).get("tool_calls"))


def decision_text(task: Any) -> str:
    """The text of the tool calls the autopilot decided to execute (the last decision in the history).

    When the Hub executed the step itself, the stored proposal may lack its tool calls and
    ``last_output`` is cut at 2000 characters; the decision event keeps the full arguments."""
    for event in reversed(list(getattr(task, "history", None) or [])):
        if isinstance(event, Mapping) and event.get("event_type") == "autopilot_decision":
            return _calls_text(event.get("tool_calls"))
    return ""


def output_answer(output: str) -> str:
    """The answer from a ``final_answer`` execution report in ``last_output``, or ``""``."""
    match = _FINAL_ANSWER_OUTPUT.search(str(output or "").strip())
    if not match:
        return ""
    try:
        value = ast.literal_eval(match.group(1))  # the executor prints the result dict with repr()
    except (ValueError, SyntaxError):
        return ""
    return _render(value.get("answer")) if isinstance(value, Mapping) else ""


def step_result(task: Any) -> str:
    """What a finished step produced: the proposal's text, else the decided tool calls' text, else a
    final_answer execution report, else ``last_output``."""
    output = str(getattr(task, "last_output", "") or "")
    return proposal_text(task) or decision_text(task) or output_answer(output) or output
