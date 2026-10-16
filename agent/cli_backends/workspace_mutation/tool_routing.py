"""DPRV ``tool_routing`` for the workspace-mutation loop's read/inspect phase.

The loop's model often falls back to blind ``repo.read_file_range`` guesses in a
large repository and exhausts its iteration budget without writing. This module
routes *read-only* tool requests through the configured decision cascade
(``tool_routing`` area: local decision, TypeSafe Jev, LLM), so a confident
decision can upgrade a blind read to the best inspection tool
(``codecompass.plan_context`` first). The existing tool policy stays the hard
gate; any abstention, deferral or error keeps today's path.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

# Only *blind* inspection picks are re-routed; a model that already chose a
# precise tool is left alone, and writes are never routed here.
_BLIND_READ_TOOLS = frozenset({"repo.read_file_range", "repo.grep", "repo.list_files"})

CODECOMPASS_READ_TOOLS: dict[str, str] = {
    "codecompass.plan_context": "Plan the relevant context files and exact line ranges for a coding task.",
    "codecompass.search": "Agentic retrieval of the code passages that answer a question.",
    "codecompass.search_symbols": "Find the symbol/definition that matches a name or term.",
}

QUESTION_KEY = "inspection_tool"
KEEP_CURRENT = "keep_current_read"
INSTRUCTIONS = (
    "A coding worker is about to read source files blindly to find where a change belongs. "
    "Choose the CodeCompass inspection tool that best locates the relevant code, or keep the current blind read."
)
DEFAULT_MIN_CONFIDENCE = 0.6


def is_blind_read_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip() in _BLIND_READ_TOOLS


def route_read_tool(*, query: str, state: str, allowed_tools: Sequence[str], mutation_mode: str = "read_only",
                    config: Mapping[str, Any] | None = None, service: Any | None = None) -> dict[str, Any] | None:
    """A confident inspection-tool candidate (``{tool_name, arguments}``) or ``None`` (keep today's path)."""
    config = config if isinstance(config, Mapping) else {}
    if str(config.get("mode") or "disabled").strip().lower() == "disabled":
        return None
    available = [tool for tool in CODECOMPASS_READ_TOOLS if tool in (allowed_tools or ())]
    if not available:
        return None
    chosen_tool = available[0]
    service = service or _service()
    try:
        if service.area_mode("tool_routing") == "off":
            return None
        from agent.services.decision_providers.types import DecisionQuestion, DecisionRequest

        threshold = float(service.area_option("tool_routing", "inspection_min_confidence",
                                              DEFAULT_MIN_CONFIDENCE) or DEFAULT_MIN_CONFIDENCE)
        options = {chosen_tool: CODECOMPASS_READ_TOOLS[chosen_tool],
                   KEEP_CURRENT: "Keep the current blind read; no inspection tool is clearly better."}
        request = DecisionRequest(state=str(state or "")[:4000], purpose="tool_routing", questions=(
            DecisionQuestion.choice(QUESTION_KEY, INSTRUCTIONS, options),))
        decision = service.decide("tool_routing", request, question_thresholds={QUESTION_KEY: threshold})
        if decision is None or not decision.usable:
            return None
        answer = decision.outcome.result.answer(QUESTION_KEY)
    except Exception:  # a routing failure must never break the loop
        return None
    if answer.choice != chosen_tool or float(answer.confidence) < threshold:
        return None
    return {"tool_name": chosen_tool, "arguments": {"query": str(query or "")[:1000]}}


def prefer_read_tool(*, session: Any, tool_name: str, arguments: dict[str, Any],
                     service: Any | None = None) -> tuple[str, dict[str, Any]]:
    """Upgrade a blind read to the routed inspection tool; otherwise return the call unchanged."""
    if not is_blind_read_tool(tool_name):
        return tool_name, arguments
    from agent.cli_backends.helpers import _get_agent_config

    agent_cfg = _get_agent_config() or {}
    tool_loop_cfg = dict(agent_cfg.get("ananta_worker_tool_loop") or {})
    routing_cfg = dict(tool_loop_cfg.get("tiny_router") or {})
    if not routing_cfg:
        return tool_name, arguments
    allowed = [str(tool) for tool in (tool_loop_cfg.get("allowed_tools") or []) if str(tool).strip()]
    if not allowed:
        return tool_name, arguments
    query = str(getattr(session, "task_prompt", "") or "")
    state = (query + "\n\nBlind read the worker is about to perform: "
             + json.dumps({"tool_name": tool_name, "arguments": arguments}, ensure_ascii=False,
                          default=str))[:4000]
    routed = route_read_tool(query=query, state=state, allowed_tools=allowed, mutation_mode=str(session.mode),
                             config=routing_cfg, service=service)
    if not routed or routed["tool_name"] == tool_name:
        return tool_name, arguments
    return routed["tool_name"], routed["arguments"]


def _service() -> Any:
    from agent.services.decision_providers.service import get_decision_service

    return get_decision_service()
