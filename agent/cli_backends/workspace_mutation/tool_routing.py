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
import logging
from typing import Any, Mapping, Sequence

log = logging.getLogger(__name__)

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


_READ_ONLY_PREFIXES = ("repo.list_files", "repo.read_file_range", "repo.grep", "codecompass.",
                       "git.status", "git.diff_readonly", "workspace.diff")

ACTION_NUDGE = (
    "You have enough context. Do not read more files: emit a `patch_request` (or `workspace_write`) "
    "now, then `test.run` with the exact allowlisted command, or a `final_answer` with the result."
)


def is_blind_read_tool(tool_name: str, arguments: Mapping[str, Any] | None = None) -> bool:
    """A read the model has not located yet.

    ``repo.read_file_range`` that already names a concrete file is a precise read, not a
    blind guess: routing it to ``codecompass.plan_context`` would hand the model locations
    instead of the file content it asked for, so it reads again and exhausts its iteration
    budget without ever being able to write (9 of 9 reads in one live propose run were
    upgraded this way and the loop ended in ``max_iterations``). Pathless searches stay
    routable -- locating is what the inspection tool is for.
    """
    name = str(tool_name or "").strip()
    if name == "repo.read_file_range":
        path = arguments.get("path") if isinstance(arguments, Mapping) else None
        return not str(path or "").strip()
    return name in _BLIND_READ_TOOLS


def is_read_only_tool(tool_name: str) -> bool:
    return any(str(tool_name or "").startswith(prefix) for prefix in _READ_ONLY_PREFIXES)


def route_read_tool(*, query: str, state: str, allowed_tools: Sequence[str], mutation_mode: str = "read_only",
                    config: Mapping[str, Any] | None = None, service: Any | None = None) -> dict[str, Any] | None:
    """A confident inspection-tool candidate (``{tool_name, arguments}``) or ``None`` (keep today's path)."""
    config = config if isinstance(config, Mapping) else {}
    if str(config.get("mode") or "disabled").strip().lower() == "disabled":
        log.info("tool_routing: skip (router mode disabled)")
        return None
    available = [tool for tool in CODECOMPASS_READ_TOOLS if tool in (allowed_tools or ())]
    if not available:
        log.info("tool_routing: skip (no codecompass read tool in allowed_tools)")
        return None
    chosen_tool = available[0]
    service = service or _service()
    try:
        if service.area_mode("tool_routing") == "off":
            log.info("tool_routing: skip (decision area off)")
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
            log.info("tool_routing: defer (decision unusable)")
            return None
        answer = decision.outcome.result.answer(QUESTION_KEY)
    except Exception as exc:  # a routing failure must never break the loop
        log.info("tool_routing: defer (decision error: %s)", type(exc).__name__)
        return None
    if answer.choice != chosen_tool or float(answer.confidence) < threshold:
        log.info("tool_routing: keep read (choice=%s conf=%.2f thr=%.2f)",
                 answer.choice, float(answer.confidence), threshold)
        return None
    return {"tool_name": chosen_tool, "arguments": {"query": str(query or "")[:1000]}}


def prefer_read_tool(*, session: Any, tool_name: str, arguments: dict[str, Any],
                     service: Any | None = None) -> tuple[str, dict[str, Any]]:
    """Upgrade a blind read to the routed inspection tool; otherwise return the call unchanged."""
    if not is_blind_read_tool(tool_name, arguments):
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
        log.info("tool_routing: keep %s (no accepted upgrade)", tool_name)
        return tool_name, arguments
    log.info("tool_routing: upgrade %s -> %s", tool_name, routed["tool_name"])
    return routed["tool_name"], routed["arguments"]


def _service() -> Any:
    from agent.services.decision_providers.service import get_decision_service

    return get_decision_service()
