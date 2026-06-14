"""Agentic tool-call loop for the rag_iterative chat path.

The LLM receives an initial context from RAG retrieval and can then
request additional files or search results via OpenAI-style tool calls.
This allows the model to proactively pull in exactly what it needs.
"""
from __future__ import annotations

import json
import logging
import pathlib as _pl
from typing import Any

_log = logging.getLogger(__name__)

_CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full content of a file from the project repository. "
                "Use this when you need to inspect a specific file that was not "
                "included in the initial context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repo-relative path to the file, e.g. agent/config.py or todos/todo-erklaer-ai-snake.jsonl",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": (
                "Search the codebase by keyword and return matching file paths. "
                "Use this when you are unsure which files contain relevant information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords, function names, class names, etc.)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 8, max 20)",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def _resolve_file(path: str, repo_root: _pl.Path) -> _pl.Path | None:
    candidate = _pl.Path(path) if path.startswith("/") else repo_root / path
    if candidate.exists() and candidate.is_file():
        return candidate
    if path.startswith("/app/"):
        candidate = repo_root / path[5:]
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _tool_read_file(path: str, repo_root: _pl.Path, max_chars: int) -> str:
    resolved = _resolve_file(path.strip(), repo_root)
    if resolved is None:
        return f"[Fehler: Datei nicht gefunden: {path}]"
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [abgeschnitten nach {max_chars} Zeichen]"
        return content
    except OSError as exc:
        return f"[Fehler beim Lesen: {exc}]"


def _tool_search_codebase(query: str, max_results: int, repo_root: _pl.Path) -> str:
    try:
        from agent.hybrid_orchestrator import RepositoryMapEngine
        engine = RepositoryMapEngine(repo_root)
        engine.build()
        chunks = engine.search(query, top_k=max(1, min(max_results, 20)))
        if not chunks:
            return "[Keine Treffer für diese Suche]"
        lines = [f"- {ch.source}  (score: {ch.score:.1f})" for ch in chunks]
        return "\n".join(lines)
    except Exception as exc:
        _log.debug("search_codebase tool failed: %s", exc)
        return f"[Suche fehlgeschlagen: {exc}]"


def _dispatch_tool(
    name: str,
    args: dict,
    *,
    repo_root: _pl.Path,
    max_chars_per_file: int,
) -> str:
    if name == "read_file":
        path = str(args.get("path") or "").strip()
        if not path:
            return "[Fehler: kein Pfad angegeben]"
        return _tool_read_file(path, repo_root, max_chars_per_file)
    if name == "search_codebase":
        query = str(args.get("query") or "").strip()
        max_r = max(1, min(int(args.get("max_results") or 8), 20))
        if not query:
            return "[Fehler: kein Suchbegriff angegeben]"
        return _tool_search_codebase(query, max_r, repo_root)
    return f"[Unbekanntes Tool: {name}]"


def run_rag_chat_tool_loop(
    *,
    messages: list[dict],
    provider: str,
    model: str | None,
    repo_root: _pl.Path,
    max_tool_calls: int = 4,
    max_chars_per_file: int = 8000,
    timeout: int = 180,
    rec: Any | None = None,
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

    Returns:
        (final_answer_text, trace_dict)
    """
    import requests

    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls

    trace: dict[str, Any] = {
        "mode": "tool_loop",
        "tool_calls_made": 0,
        "tools_used": [],
    }

    urls = _runtime_provider_urls()
    base_url = str(urls.get(provider) or "").rstrip("/")
    api_key = _runtime_api_key(provider)

    if not base_url:
        trace["error"] = f"no_url_for_provider:{provider}"
        return "", trace

    endpoint = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    current_messages = list(messages)
    tool_call_count = 0
    llm_call_count = 0
    last_content = ""

    def _input_preview(msgs: list[dict]) -> str:
        """Format last user/system messages as readable preview."""
        parts = []
        for m in msgs[-4:]:
            role = str(m.get("role") or "")
            content = str(m.get("content") or "")
            if content:
                parts.append(f"[{role}]\n{content[:2000]}")
        return "\n\n---\n\n".join(parts)

    for _iteration in range(max_tool_calls + 2):
        use_tools = tool_call_count < max_tool_calls
        llm_call_count += 1
        payload: dict[str, Any] = {
            "model": model or "auto",
            "messages": current_messages,
        }
        if use_tools:
            payload["tools"] = _CHAT_TOOLS
            payload["tool_choice"] = "auto"

        label = (
            f"LLM-Call {llm_call_count} (Tool-Loop, {len(current_messages)} Msgs)"
            if use_tools else
            f"LLM-Call {llm_call_count} (Finale Antwort)"
        )
        if rec:
            rec.event(
                f"tool_loop_llm_{llm_call_count}",
                label,
                status="running",
                details={"iteration": _iteration, "messages": len(current_messages), "use_tools": use_tools},
                input_preview=_input_preview(current_messages),
            )

        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _log.warning("tool_loop: LLM call failed: %s", exc)
            trace["error"] = f"llm_call_failed: {exc}"
            if rec:
                rec.event(
                    f"tool_loop_llm_{llm_call_count}_done",
                    f"{label} — Fehler",
                    status="failed",
                    details={"error": str(exc)},
                )
            return last_content, trace

        try:
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            finish_reason = str(choice.get("finish_reason") or "")
        except Exception:
            trace["error"] = "invalid_llm_response"
            return last_content, trace

        content = str(msg.get("content") or "").strip()
        tool_calls = list(msg.get("tool_calls") or [])
        last_content = content or last_content

        if rec:
            tc_names = [
                str((tc.get("function") or {}).get("name") or "?")
                for tc in tool_calls
            ]
            rec.event(
                f"tool_loop_llm_{llm_call_count}_done",
                f"{label} — {'Tool-Calls: ' + ', '.join(tc_names) if tc_names else 'Antwort erhalten'}",
                status="completed",
                details={
                    "finish_reason": finish_reason,
                    "tool_calls_requested": tc_names,
                    "answer_chars": len(content),
                },
                output_preview=content if content else (f"→ Tool-Calls: {tc_names}" if tc_names else None),
            )

        if not tool_calls or finish_reason == "stop" or not use_tools:
            trace["final_finish_reason"] = finish_reason
            return content, trace

        # Add assistant message with tool_calls to history
        current_messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tool_call_count += 1
            tc_id = str(tc.get("id") or f"call_{tool_call_count}")
            fn = tc.get("function") or {}
            fn_name = str(fn.get("name") or "")

            try:
                args = json.loads(str(fn.get("arguments") or "{}"))
            except Exception:
                args = {}

            _log.debug("tool_loop: calling %s(%s)", fn_name, args)
            result = _dispatch_tool(
                fn_name, args,
                repo_root=repo_root,
                max_chars_per_file=max_chars_per_file,
            )

            trace["tools_used"].append({
                "iteration": _iteration,
                "name": fn_name,
                "args": {k: str(v)[:120] for k, v in args.items()},
                "result_chars": len(result),
            })
            trace["tool_calls_made"] = tool_call_count

            if rec:
                rec.event(
                    f"tool_call_{tool_call_count}",
                    f"Tool: {fn_name}({', '.join(f'{k}={v!r}' for k, v in list(args.items())[:2])})",
                    status="completed",
                    details={
                        "function": fn_name,
                        "args": args,
                        "result_chars": len(result),
                    },
                    output_preview=result[:500] if result else None,
                )

            current_messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })

        if tool_call_count >= max_tool_calls:
            current_messages.append({
                "role": "user",
                "content": "Bitte gib jetzt deine abschließende Antwort auf Basis aller gesammelten Informationen.",
            })

    return last_content, trace
