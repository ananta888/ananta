"""The result of a long-context step as the next step or the original task needs it (LCTX-010).

In the autopilot's single-shot flow an analysis step hands its answer over in the proposal:
as the ``content`` of a ``file_write`` or as the text fields of a result call (``summary``,
``answer``, ...). ``last_output`` then only reports "file written". The Hub keeps the
proposal, so the answer is read from there -- no shared workspace between containers --
and ``last_output`` stays the fallback when the proposal carries no text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

TEXT_FIELDS = ("content", "answer", "result", "summary", "text", "details", "findings", "points")


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


def proposal_text(task: Any) -> str:
    """The text the step's proposal carried in its tool-call arguments, or ``""``."""
    parts: list[str] = []
    for call in _proposal(task).get("tool_calls") or []:
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


def step_result(task: Any) -> str:
    """What a finished step produced: the proposal's text if it has one, else ``last_output``."""
    return proposal_text(task) or str(getattr(task, "last_output", "") or "")
