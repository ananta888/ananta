"""LangChain tool façades backed exclusively by the common Hub-gated pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from worker.core.tool_calling_pipeline import ToolCallingPipeline, ToolCallRequest

_BUILTIN_TOOL_NAMES = frozenset({"summarize_doc", "search_code"})
ToolRequestFactory = Callable[[str, dict[str, Any]], ToolCallRequest]


def get_tools_for_chain(
    allowed_tools: list[str],
    *,
    pipeline: ToolCallingPipeline | None = None,
    request_factory: ToolRequestFactory | None = None,
) -> list[Any]:
    """Build LangChain façades only when the common pipeline is configured.

    Older code directly called CodeCompass or ``generate_text`` from these
    functions.  That was a policy/ledger bypass.  Returning no tools is the
    backward-compatible, fail-closed behavior for unconfigured callers.
    """

    if not allowed_tools or pipeline is None or request_factory is None:
        return []
    try:
        from langchain_core.tools import tool as lc_tool  # type: ignore
    except ImportError:
        return []

    from worker.adapters.workflow_policy_gate import _ALWAYS_BLOCKED_TOOLS

    result: list[Any] = []
    for tool_name in allowed_tools:
        if tool_name in _ALWAYS_BLOCKED_TOOLS or tool_name not in _BUILTIN_TOOL_NAMES:
            continue
        if tool_name == "search_code":
            result.append(_make_search_code_tool(lc_tool, pipeline, request_factory))
        elif tool_name == "summarize_doc":
            result.append(_make_summarize_doc_tool(lc_tool, pipeline, request_factory))
    return result


def _execute(
    pipeline: ToolCallingPipeline,
    request_factory: ToolRequestFactory,
    tool_id: str,
    arguments: dict[str, Any],
) -> str:
    outcome = pipeline.execute(request_factory(tool_id, arguments))
    if outcome.status != "success" or outcome.result is None:
        return json.dumps(
            {
                "status": "unsupported" if outcome.status == "blocked" else "failed",
                "reason_code": outcome.reason_code,
                "operation_id": outcome.operation_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    return json.dumps(
        dict(outcome.result),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _make_search_code_tool(
    lc_tool_decorator: Any,
    pipeline: ToolCallingPipeline,
    request_factory: ToolRequestFactory,
) -> Any:
    @lc_tool_decorator
    def search_code(query: str) -> str:
        """Search code through Ananta's authorized common tool pipeline."""

        return _execute(pipeline, request_factory, "search_code", {"query": query})

    return search_code


def _make_summarize_doc_tool(
    lc_tool_decorator: Any,
    pipeline: ToolCallingPipeline,
    request_factory: ToolRequestFactory,
) -> Any:
    @lc_tool_decorator
    def summarize_doc(text: str) -> str:
        """Summarize text through Ananta's authorized common tool pipeline."""

        return _execute(pipeline, request_factory, "summarize_doc", {"text": text})

    return summarize_doc


__all__ = ["ToolRequestFactory", "get_tools_for_chain"]
