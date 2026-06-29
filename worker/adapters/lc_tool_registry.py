"""Built-in Tool Registry für LangChain-Integration (LCG-052).

Definiert eine kleine Menge erlaubter Built-in-Tools als @tool-dekorierte
Funktionen. Diese dürfen in Chain-Descriptoren als allowed_tools referenziert werden.
"""
from __future__ import annotations

from typing import Any


_BUILTIN_TOOL_NAMES = frozenset({"summarize_doc", "search_code"})


def get_tools_for_chain(allowed_tools: list[str]) -> list[Any]:
    """Return list of BaseTool instances for the given allowed_tools names.

    Filters against built-in tool registry AND WorkflowPolicyGate hard-deny list.
    Returns empty list if langchain-core not installed.
    """
    if not allowed_tools:
        return []

    try:
        from langchain_core.tools import tool as lc_tool  # type: ignore
    except ImportError:
        return []

    from worker.adapters.workflow_policy_gate import _ALWAYS_BLOCKED_TOOLS

    result = []
    for tool_name in allowed_tools:
        if tool_name in _ALWAYS_BLOCKED_TOOLS:
            continue
        if tool_name not in _BUILTIN_TOOL_NAMES:
            continue
        if tool_name == "search_code":
            result.append(_make_search_code_tool(lc_tool))
        elif tool_name == "summarize_doc":
            result.append(_make_summarize_doc_tool(lc_tool))
    return result


def _make_search_code_tool(lc_tool_decorator: Any) -> Any:
    @lc_tool_decorator
    def search_code(query: str) -> str:
        """Search the codebase using CodeCompass for relevant code snippets."""
        try:
            from worker.retrieval.codecompass_retriever import CodeCompassRetriever
            retriever = CodeCompassRetriever(scope="codecompass_vector")
            result = retriever.query(query, max_results=5)
            sources = result.get("sources", [])
            if not sources:
                return "No results found."
            return "\n\n".join(
                f"[{s.get('path', '')}]\n{s.get('content', '')[:400]}"
                for s in sources
            )
        except Exception as exc:
            return f"search_code error: {exc}"
    return search_code


def _make_summarize_doc_tool(lc_tool_decorator: Any) -> Any:
    @lc_tool_decorator
    def summarize_doc(text: str) -> str:
        """Summarize a document using the local LLM."""
        try:
            from agent.llm_integration import generate_text
            prompt = f"Summarize the following text in 2-3 sentences:\n\n{text[:2000]}"
            result = generate_text(prompt=prompt, model=None, timeout=30)
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                return str(result.get("text") or result.get("content") or "")
            return str(result)
        except Exception as exc:
            return f"summarize_doc error: {exc}"
    return summarize_doc
