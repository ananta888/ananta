"""Agentic tool-call loop for the rag_iterative chat path.

The LLM receives an initial context from RAG retrieval and can then
request additional files or search results via OpenAI-style tool calls.
This allows the model to proactively pull in exactly what it needs.

Module layout: this module is the entry point and keeps the historical
aliases; the loop itself is composed from
:mod:`.snakes_rag_tool_loop_state` (options, state, transport, evidence),
:mod:`.snakes_rag_tool_loop_tools` (tool-call execution) and
:mod:`.snakes_rag_tool_loop_session` (iteration control).
"""
from __future__ import annotations

import logging
import pathlib as _pl
from typing import Any, Callable

from agent.routes import snakes_rag_text_protocol as _text_protocol
from agent.routes import snakes_rag_tools as _rag_tools
from agent.routes.snakes_rag_tool_loop_session import (
    UNLIMITED_TOOL_LOOP_MAX_ITERATIONS,
    RagToolLoopSession,
)
from agent.routes.snakes_rag_tool_loop_state import (
    EvidenceMemory,
    RagLlmTransport,
    RagToolLoopOptions,
    RagToolLoopState,
)
from agent.routes.snakes_rag_tool_loop_tools import ToolCallExecutor

_log = logging.getLogger(__name__)

_CHAT_TOOLS = _rag_tools._CHAT_TOOLS
_CODECOMPASS_CHAT_TOOL_MAP = _rag_tools._CODECOMPASS_CHAT_TOOL_MAP
_dispatch_tool = _rag_tools._dispatch_tool
_tool_read_file = _rag_tools._tool_read_file
_tool_search_codebase = _rag_tools._tool_search_codebase
compact_initial_packed_context = _text_protocol.compact_initial_packed_context
format_evidence_prompt = _text_protocol.format_evidence_prompt
retire_initial_next_step_instruction = _text_protocol.retire_initial_next_step_instruction
_full_prompt = _text_protocol.full_prompt
_input_preview = _text_protocol.input_preview
_looks_like_tool_request = _text_protocol.looks_like_tool_request
_parse_file_sections = _text_protocol.parse_file_sections
_parse_textual_tool_calls = _text_protocol.parse_textual_tool_calls
_total_context_chars = _text_protocol.total_context_chars

_UNLIMITED_TOOL_LOOP_MAX_ITERATIONS = UNLIMITED_TOOL_LOOP_MAX_ITERATIONS


def run_rag_chat_tool_loop(
    *,
    messages: list[dict],
    provider: str,
    model: str | None,
    api_base: str | None = None,
    repo_root: _pl.Path,
    max_tool_calls: int = 0,
    max_search_calls: int = 0,
    max_chars_per_file: int = 8000,
    config_provider: Callable[[], dict[str, Any]] | None = None,
    timeout: int = 180,
    rec: Any | None = None,
    initial_files: list[str] | None = None,
    question: str = "",
    summarize_reads: bool = False,
    max_summary_chars: int = 600,
    initial_evidence: list[dict[str, Any]] | None = None,
    architecture_context: str = "",
    cancel_event: Any | None = None,
    final_task_kind: str = "repo_analysis",
    lock_tool_budgets: bool = False,
) -> tuple[str, dict[str, Any]]:
    """
    Agentic loop: send messages to LLM, handle tool calls, return final answer.

    Args:
        messages: Full message list (system + history + user question + initial context).
        provider: LLM provider (lmstudio, ollama, ...).
        model: Model ID.
        repo_root: Absolute path to repository root.
        max_tool_calls: Maximum number of tool calls before forcing a final answer.
        max_chars_per_file: Max characters to return per file read.
        timeout: HTTP timeout per LLM call in seconds.
        rec: Optional trace recorder.
        initial_files: List of file paths included in initial context (for logging).
        initial_evidence: Files already packed into the initial prompt.

    Returns:
        (final_answer_text, trace_dict)

    This function is the composition root of the loop: it resolves the
    provider/profile routing and injects the collaborators (tool dispatch,
    worker-profile chat) into :class:`RagToolLoopSession`.
    """
    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls
    from agent.routes.snakes_worker_routing import (
        _worker_profile_chat,
        snake_profile_routing_enabled,
    )

    # 0 = truly unlimited; the loop still exits when the model stops calling tools
    state = RagToolLoopState.start(
        messages,
        max_tool_calls=max_tool_calls if max_tool_calls > 0 else 0,
        max_search_calls=max_search_calls,
    )
    trace = state.trace

    base_url = str(api_base or _runtime_provider_urls().get(provider) or "").rstrip("/")
    api_key = _runtime_api_key(provider)
    use_profile_routing = snake_profile_routing_enabled()
    trace["inference_route"] = "hub_worker_local_profiles" if use_profile_routing else "legacy_direct_provider"
    if not base_url and not use_profile_routing:
        trace["error"] = f"no_url_for_provider:{provider}"
        return "", trace

    options = RagToolLoopOptions(
        provider=provider,
        model=model,
        repo_root=repo_root,
        max_chars_per_file=max_chars_per_file,
        config_provider=config_provider,
        timeout=timeout,
        rec=rec,
        initial_files=initial_files,
        question=question,
        summarize_reads=summarize_reads,
        max_summary_chars=max_summary_chars,
        initial_evidence=initial_evidence,
        architecture_context=architecture_context,
        cancel_event=cancel_event,
        final_task_kind=final_task_kind,
        lock_tool_budgets=lock_tool_budgets,
    )
    transport = RagLlmTransport(
        use_profile_routing=use_profile_routing,
        base_url=base_url,
        api_key=api_key,
        worker_profile_chat=_worker_profile_chat,
        trace=trace,
    )
    evidence = EvidenceMemory(options, state, transport)
    # ``_dispatch_tool`` is resolved on this module at call time (test seam kept).
    tools = ToolCallExecutor(options, state, evidence, _dispatch_tool)
    return RagToolLoopSession(options, state, transport, evidence, tools).run()
