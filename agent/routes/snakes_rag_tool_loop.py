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

from agent.utils import log_llm_entry

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
        # Try to find the file by name anywhere in the repo
        filename = _pl.Path(path.strip()).name
        candidates: list[str] = []
        _skip = {"__pycache__", ".git", ".claude", "node_modules", "dist", ".venv", "venv"}
        try:
            for p in repo_root.rglob(filename):
                if p.is_file() and not any(part in _skip for part in p.parts):
                    candidates.append(str(p.relative_to(repo_root)))
                    if len(candidates) >= 3:
                        break
        except Exception:
            pass
        hint = (
            f"\n[Korrekter Pfad: nutze read_file('{candidates[0]}') — "
            f"Datei gefunden unter: {', '.join(candidates)}]"
            if candidates else ""
        )
        return f"[Fehler: Datei nicht gefunden: {path}]{hint}"
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
    max_tool_calls: int = 0,
    max_chars_per_file: int = 8000,
    timeout: int = 180,
    rec: Any | None = None,
    initial_files: list[str] | None = None,
    question: str = "",
    summarize_reads: bool = False,
    max_summary_chars: int = 600,
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

    Returns:
        (final_answer_text, trace_dict)
    """
    import requests

    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls

    # 0 or negative = unlimited (capped at 50 to prevent infinite loops)
    _effective_max = max_tool_calls if max_tool_calls > 0 else 50
    max_tool_calls = _effective_max

    trace: dict[str, Any] = {
        "mode": "tool_loop",
        "tool_calls_made": 0,
        "tools_used": [],
        "max_tool_calls_effective": max_tool_calls,
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
    _already_read: dict[str, str] = {}  # path → content, to prevent re-reading the same file

    def _summarize_file(path: str, content: str) -> str:
        """Intermediate LLM call: extract question-relevant info from a file into a compact summary."""
        if not question or len(content) < 800:
            return content  # too short to bother summarizing
        q = question[:300]
        summary_prompt = (
            f"Frage: {q}\n\n"
            f"Datei: {path}\n"
            f"```\n{content[:12000]}\n```\n\n"
            f"Extrahiere AUSSCHLIESSLICH die Informationen aus dieser Datei, die zur Frage direkt relevant sind. "
            f"Maximal {max_summary_chars} Zeichen. "
            f"Falls nichts relevant: '[nicht relevant]'."
        )
        try:
            import requests as _req
            resp = _req.post(
                endpoint,
                json={"model": model or "auto", "messages": [{"role": "user", "content": summary_prompt}]},
                headers=headers,
                timeout=min(timeout, 60),
            )
            resp.raise_for_status()
            summary = str(
                ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if summary:
                return f"[Zusammenfassung von {path}]\n{summary[:max_summary_chars]}"
        except Exception as _exc:
            _log.debug("summarize_file failed for %s: %s", path, _exc)
        return content  # fallback: full content

    def _input_preview(msgs: list[dict], max_chars: int = 2000) -> str:
        """Short preview of the last 4 messages — for log entries only."""
        parts = []
        for m in msgs[-4:]:
            role = str(m.get("role") or "")
            content = str(m.get("content") or "")
            if content:
                parts.append(f"[{role}]\n{content[:max_chars]}")
        return "\n\n---\n\n".join(parts)

    def _full_prompt(msgs: list[dict]) -> str:
        """Format ALL messages exactly as sent to the LLM — no truncation."""
        parts = []
        for i, m in enumerate(msgs):
            role = str(m.get("role") or "")
            content = str(m.get("content") or "")
            if not content and m.get("tool_calls"):
                tc_names = [
                    str((tc.get("function") or {}).get("name") or "?")
                    for tc in m["tool_calls"]
                ]
                tc_args = [
                    str((tc.get("function") or {}).get("arguments") or "")
                    for tc in m["tool_calls"]
                ]
                content = "\n".join(
                    f"→ tool_call: {name}({args})"
                    for name, args in zip(tc_names, tc_args)
                )
            if content:
                parts.append(f"[{role} #{i+1}]\n{content}")
        sep = "\n\n" + "=" * 60 + "\n\n"
        return sep.join(parts)

    def _total_context_chars(msgs: list[dict]) -> int:
        return sum(len(str(m.get("content") or "")) for m in msgs)

    def _parse_file_sections(user_content: str) -> list[dict[str, Any]]:
        """Parse the '=== Verfügbare Dateien ===' block → list of {path, score}."""
        import re
        marker = "=== Verfügbare Dateien"
        idx = user_content.find(marker)
        if idx < 0:
            # Fallback: old format with ### file blocks
            sections = re.split(r"\n### ", "\n" + user_content)
            return [{"path": s.partition("\n")[0].strip(), "chars": len(s.partition("\n")[2])}
                    for s in sections[1:]]
        block_start = user_content.find("\n", idx) + 1
        block_end = user_content.find("\n\n", block_start)
        block = user_content[block_start:block_end if block_end > 0 else block_start + 4000]
        result = []
        for line in block.splitlines():
            m = re.match(r"\s*\d+\.\s+(.+?)\s+\(relevanz:\s*([\d.]+)\)", line)
            if m:
                result.append({"path": m.group(1).strip(), "score": float(m.group(2))})
        return result

    # --- Pre-loop: log initial context summary and write context dump file ---
    _initial_user_content = str((current_messages[-1] or {}).get("content") or "")
    _ctx_total_chars = _total_context_chars(current_messages)
    _file_sections = _parse_file_sections(_initial_user_content) if initial_files else []

    # Write full initial context to dump file (overwrites each run for easy inspection)
    try:
        from agent.utils import get_data_dir
        _dump_path = _pl.Path(get_data_dir()) / "last_llm_context.txt"
        _dump_path.write_text(_initial_user_content, encoding="utf-8")
    except Exception as _dump_exc:
        _log.debug("context dump failed: %s", _dump_exc)

    log_llm_entry(
        event="tool_loop_context_summary",
        provider=provider,
        model=model or "auto",
        total_context_chars=_ctx_total_chars,
        initial_files_count=len(initial_files or []),
        file_sections=_file_sections,
    )
    if rec:
        rec.event(
            "tool_loop_initial_context",
            f"Initialer Kontext: {len(initial_files or [])} Dateien, {_ctx_total_chars:,} Zeichen",
            status="info",
            details={
                "files": _file_sections,
                "total_context_chars": _ctx_total_chars,
                "context_dump": "data/last_llm_context.txt",
            },
            input_preview="\n".join(
                "{}.  {}  (relevanz: {})".format(i, s["path"], s.get("score", s.get("chars", "?")))
                for i, s in enumerate(_file_sections, 1)
            ) or "(keine Dateien)",
        )

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

        _ctx_chars = _total_context_chars(current_messages)
        label = (
            f"LLM-Call {llm_call_count} (Tool-Loop, {len(current_messages)} Msgs, ~{_ctx_chars//1000}K Zeichen)"
            if use_tools else
            f"LLM-Call {llm_call_count} (Finale Antwort, ~{_ctx_chars//1000}K Zeichen)"
        )

        _prompt_text = _input_preview(current_messages)

        if rec:
            # Show ALL messages exactly as sent to the LLM (each capped at 10K chars)
            rec.event(
                f"tool_loop_llm_{llm_call_count}",
                label,
                status="running",
                details={
                    "iteration": _iteration,
                    "messages": len(current_messages),
                    "use_tools": use_tools,
                    "context_chars": _ctx_chars,
                },
                input_preview=_full_prompt(current_messages),
            )

        _log_kwargs: dict[str, Any] = dict(
            event="llm_call_start",
            provider=provider,
            model=model or "auto",
            prompt=_prompt_text,
            tool_loop_call=llm_call_count,
            history_len=len(current_messages),
            context_chars=_ctx_chars,
        )
        if llm_call_count == 1 and initial_files:
            _log_kwargs["initial_files"] = initial_files
            _log_kwargs["initial_files_count"] = len(initial_files)
        log_llm_entry(**_log_kwargs)

        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _log.warning("tool_loop: LLM call failed: %s", exc)
            trace["error"] = f"llm_call_failed: {exc}"
            log_llm_entry(
                event="llm_call_end",
                provider=provider,
                model=model or "auto",
                success=False,
                tool_loop_call=llm_call_count,
                response="",
                error=str(exc),
            )
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

        _tc_names_log = [
            str((tc.get("function") or {}).get("name") or "?") for tc in tool_calls
        ]
        log_llm_entry(
            event="llm_call_end",
            provider=provider,
            model=model or "auto",
            success=True,
            tool_loop_call=llm_call_count,
            finish_reason=finish_reason,
            response=content[:2000] if content else (f"→ tool_calls: {_tc_names_log}" if _tc_names_log else ""),
            tool_calls=_tc_names_log,
        )

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
            if fn_name == "read_file":
                _req_path = str(args.get("path") or "").strip()
                if _req_path in _already_read:
                    result = (
                        f"[Datei '{_req_path}' wurde bereits gelesen — "
                        f"der Inhalt ist bereits im Kontext. "
                        f"Bitte eine andere Datei aus der Liste auswählen oder search_codebase() nutzen.]"
                    )
                else:
                    result = _dispatch_tool(
                        fn_name, args,
                        repo_root=repo_root,
                        max_chars_per_file=max_chars_per_file,
                    )
                    if not result.startswith("[Fehler"):
                        _already_read[_req_path] = result
                        if summarize_reads:
                            _raw_chars = len(result)
                            if rec:
                                rec.event(
                                    f"tool_call_{tool_call_count}_summarize",
                                    f"Zusammenfasse: {_req_path}",
                                    status="running",
                                    details={"path": _req_path, "raw_chars": _raw_chars},
                                )
                            result = _summarize_file(_req_path, result)
                            if rec:
                                rec.event(
                                    f"tool_call_{tool_call_count}_summarize",
                                    f"Zusammengefasst: {_req_path} ({_raw_chars} → {len(result)} Zeichen)",
                                    status="completed",
                                    details={"path": _req_path, "raw_chars": _raw_chars, "summary_chars": len(result)},
                                    output_preview=result,
                                )
            else:
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
