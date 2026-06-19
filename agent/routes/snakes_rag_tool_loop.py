"""Agentic tool-call loop for the rag_iterative chat path.

The LLM receives an initial context from RAG retrieval and can then
request additional files or search results via OpenAI-style tool calls.
This allows the model to proactively pull in exactly what it needs.
"""
from __future__ import annotations

import json
import logging
import pathlib as _pl
import re
from typing import Any, Callable

from agent.utils import log_llm_entry
from agent.services.snake_chat_cancellation import is_chat_cancelled
from agent.services.rag_context_packer import should_skip_initial_pack

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
        chunks = [
            ch for ch in engine.search(query, top_k=max(1, min(max_results * 3, 40)))
            if not should_skip_initial_pack(str(ch.source or ""))
        ][:max(1, min(max_results, 20))]
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
    cancel_event: Any | None = None,
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
    """
    import requests

    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls

    # 0 = truly unlimited; the loop still exits when the model stops calling tools
    _effective_max = max_tool_calls if max_tool_calls > 0 else 0
    max_tool_calls = _effective_max

    trace: dict[str, Any] = {
        "mode": "tool_loop",
        "tool_calls_made": 0,
        "tools_used": [],
        "evidence": [],
        "max_tool_calls_effective": max_tool_calls if max_tool_calls > 0 else "unlimited",
        "max_search_calls_effective": max_search_calls if max_search_calls > 0 else "unlimited",
    }

    urls = _runtime_provider_urls()
    base_url = str(urls.get(provider) or "").rstrip("/")
    api_key = _runtime_api_key(provider)

    if not base_url:
        trace["error"] = f"no_url_for_provider:{provider}"
        return "", trace

    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    current_messages = list(messages)
    tool_call_count = 0
    llm_call_count = 0
    last_content = ""
    _already_read: dict[str, str] = {}  # path → content, to prevent re-reading the same file
    _already_searched: set[str] = set()
    _evidence: dict[str, dict[str, Any]] = {}
    search_call_count = 0
    force_final_next = False
    final_repair_attempts = 0
    last_non_tool_content = ""

    def _cancelled() -> bool:
        if not is_chat_cancelled(cancel_event):
            return False
        trace["cancelled"] = True
        trace["error"] = "cancelled"
        return True

    def _looks_like_tool_request(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        lowered = value.lower()
        if "[tool_request]" in lowered or "[end_tool_request]" in lowered:
            return True
        if re.search(r'"(?:name|tool_name)"\s*:\s*"(?:read_file|search_codebase)"', value):
            return True
        if re.search(r'"tool_calls"\s*:', value):
            return True
        return False

    def _summarize_file(path: str, content: str) -> str:
        """Intermediate LLM call: extract question-relevant info from a file into a compact summary."""
        if _cancelled():
            return "[Abgebrochen]"
        if not question or len(content) < 200:
            return content  # too short to bother summarizing
        q = question[:300]
        # Cap input at 5000 chars to keep the summarization call fast
        content_for_summary = content[:5000]
        summary_prompt = (
            f"Frage: {q}\n\n"
            f"Datei: {path}\n"
            f"```\n{content_for_summary}\n```\n\n"
            f"Extrahiere AUSSCHLIESSLICH die Informationen aus dieser Datei, die zur Frage direkt relevant sind. "
            f"Nenne konkrete Symbole, Funktionen, Klassen und Zeilenbezuege. "
            f"Maximal {max_summary_chars} Zeichen. "
            f"Falls nichts relevant: '[nicht relevant]'."
        )
        try:
            import requests as _req
            resp = _req.post(
                endpoint,
                json={"model": model or "auto", "messages": [{"role": "user", "content": summary_prompt}]},
                headers=headers,
                timeout=min(timeout, 120),
            )
            resp.raise_for_status()
            if _cancelled():
                return "[Abgebrochen]"
            summary = str(
                ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if summary:
                return f"[Zusammenfassung von {path}]\n{summary[:max_summary_chars]}"
        except Exception as _exc:
            _log.warning("summarize_file failed for %s: %s", path, _exc)
        return content[:max_summary_chars]  # fallback: truncated raw content

    def _remember_file(path: str, content: str, *, source: str, score: Any = None) -> None:
        compact = content.strip()
        if len(compact) > max_summary_chars:
            compact = compact[:max_summary_chars] + f"\n... [Evidence gekuerzt nach {max_summary_chars} Zeichen]"
        _evidence[path] = {
            "path": path,
            "summary": compact,
            "score": score,
            "source": source,
            "chars": len(content),
        }
        trace["evidence"] = list(_evidence.values())

    def _register_initial_evidence() -> None:
        for idx, item in enumerate(initial_evidence or [], 1):
            if _cancelled():
                return
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            content = str(item.get("content") or "").strip()
            fallback_summary = str(item.get("summary") or "").strip()
            summary = fallback_summary or "Datei wurde im Initialkontext bereitgestellt."
            if summarize_reads and content:
                if rec:
                    rec.event(
                        f"initial_context_{idx}_summarize",
                        f"Initialkontext zusammenfassen: {path}",
                        status="running",
                        details={"path": path, "raw_chars": len(content)},
                    )
                summary = _summarize_file(path, content)
                if rec:
                    rec.event(
                        f"initial_context_{idx}_summarize",
                        f"Initialkontext zusammengefasst: {path}",
                        status="completed",
                        details={"path": path, "raw_chars": len(content), "summary_chars": len(summary)},
                        output_preview=summary,
                    )
            _evidence[path] = {
                "path": path,
                "summary": summary,
                "score": item.get("score"),
                "source": item.get("source") or "initial_context",
                "chars": item.get("chars") or len(content),
            }
            _already_read[path] = f"[Datei '{path}' ist bereits im Initialkontext enthalten.]\n{summary}"
        trace["evidence"] = list(_evidence.values())

    def _evidence_prompt() -> str:
        if not _evidence:
            return ""
        lines = [
            "Recherche-Stand fuer die naechste LLM-Aktion:",
            "Verwende diese fragebezogenen Zusammenfassungen als Arbeitsgedaechtnis.",
            "Bereits gelesene oder im Initialkontext bereitgestellte Dateien:",
        ]
        for idx, item in enumerate(_evidence.values(), 1):
            score = item.get("score")
            score_txt = f", relevanz: {float(score):.1f}" if isinstance(score, int | float) else ""
            lines.append(
                f"{idx}. {item['path']} ({item.get('source')}{score_txt})\n"
                f"   {item.get('summary')}"
            )
        q_hint = f" Beantworte dann konkret: {question[:200]}" if question else ""
        lines.append(
            "Wenn noch Informationen fehlen, lies gezielt eine weitere Datei, die eine offene Frage klaert. "
            "Nutze search_codebase nur fuer voellig neue Begriffe, die in keiner Evidenz-Datei erwaehnt sind. "
            f"Wenn die Evidenz reicht, antworte jetzt abschliessend.{q_hint}"
        )
        return "\n".join(lines)

    def _compact_initial_packed_context() -> None:
        """Remove bulky initial packed file bodies from follow-up LLM calls."""
        if not _evidence:
            return
        marker = "=== Bereits gelesene CodeCompass-Top-Treffer ==="
        next_marker = "=== Verfügbare Dateien"
        replacement = (
            "=== Bereits gelesene CodeCompass-Top-Treffer (kompakt) ===\n"
            "Die Volltexte wurden fuer Folgeaufrufe entfernt. "
            "Nutze den aktuellen Recherche-Stand in der letzten User-Nachricht.\n\n"
        )
        for msg in current_messages:
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "")
            start = content.find(marker)
            if start < 0:
                continue
            end = content.find(next_marker, start)
            if end < 0:
                end = start + len(marker)
            msg["content"] = content[:start] + replacement + content[end:]
            trace["initial_context_compacted_for_followups"] = True
            return

    def _is_evidence_message(msg: dict[str, Any]) -> bool:
        return (
            msg.get("role") == "user"
            and str(msg.get("content") or "").startswith("Recherche-Stand fuer die naechste LLM-Aktion:")
        )

    def _replace_or_append_evidence_message(evidence_text: str) -> None:
        if not evidence_text:
            return
        current_messages[:] = [msg for msg in current_messages if not _is_evidence_message(msg)]
        current_messages.append({"role": "user", "content": evidence_text})

    _register_initial_evidence()
    _compact_initial_packed_context()
    _replace_or_append_evidence_message(_evidence_prompt())

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
        if _cancelled():
            if rec:
                rec.event(
                    "tool_loop_cancelled",
                    "Tool-Loop abgebrochen",
                    status="cancelled",
                    details=trace,
                )
            return "", trace

        if config_provider is not None:
            try:
                _live = config_provider()
                _new_max_tc = max(0, int(_live.get("rag_iterative_max_tool_calls") or 0))
                _new_max_sc = max(0, int(_live.get("rag_iterative_max_search_calls") or 0))
                if _new_max_tc != max_tool_calls:
                    max_tool_calls = _new_max_tc
                    trace["max_tool_calls_effective"] = max_tool_calls if max_tool_calls > 0 else "unlimited"
                if _new_max_sc != max_search_calls:
                    max_search_calls = _new_max_sc
                    trace["max_search_calls_effective"] = max_search_calls if max_search_calls > 0 else "unlimited"
            except Exception:
                pass

        search_only_exhausted = (
            max_search_calls > 0
            and search_call_count >= max_search_calls
            and not any(item.get("name") == "read_file" for item in trace.get("tools_used", []))
        )
        if search_only_exhausted:
            force_final_next = True
        use_tools = (max_tool_calls == 0 or tool_call_count < max_tool_calls) and not force_final_next
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
                    "tool_call_mode": "native_api" if use_tools else "disabled",
                    "registered_tools": [t["function"]["name"] for t in _CHAT_TOOLS] if use_tools else [],
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
            tool_call_mode="native_api" if use_tools else "disabled",
            registered_tools=[t["function"]["name"] for t in _CHAT_TOOLS] if use_tools else [],
        )
        if llm_call_count == 1 and initial_files:
            _log_kwargs["initial_files"] = initial_files
            _log_kwargs["initial_files_count"] = len(initial_files)
        log_llm_entry(**_log_kwargs)

        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            if _cancelled():
                if rec:
                    rec.event(
                        f"tool_loop_llm_{llm_call_count}_cancelled",
                        f"{label} — abgebrochen",
                        status="cancelled",
                    )
                return "", trace
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
        textual_tool_request = _looks_like_tool_request(content)
        if content and not textual_tool_request:
            last_non_tool_content = content

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
            tc_details = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                raw_args = str(fn.get("arguments") or "{}")
                try:
                    parsed_args = json.loads(raw_args)
                except Exception:
                    parsed_args = {"_raw": raw_args}
                tc_details.append({
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or "?"),
                    "arguments": parsed_args,
                    "raw_arguments": raw_args[:2000],
                })
            rec.event(
                f"tool_loop_llm_{llm_call_count}_done",
                f"{label} — {'Tool-Calls: ' + ', '.join(tc_names) if tc_names else 'Antwort erhalten'}",
                status="completed",
                details={
                    "finish_reason": finish_reason,
                    "tool_calls_requested": tc_names,
                    "tool_call_details": tc_details,
                    "answer_chars": len(content),
                },
                output_preview=content if content else (
                    "\n".join(
                        f"→ Tool-Call: {item['name']}({item['raw_arguments']})"
                        for item in tc_details
                    )
                    if tc_details else None
                ),
            )
            if textual_tool_request:
                rec.event(
                    f"tool_loop_llm_{llm_call_count}_textual_tool_request",
                    "Textueller Tool-Request im Modelltext erkannt (Fallback-Pfad)",
                    status="blocked" if (not use_tools or finish_reason == "stop" or not tool_calls) else "warning",
                    details={
                        "tool_call_mode": "textual_fallback",
                        "finish_reason": finish_reason,
                        "use_tools": use_tools,
                        "tool_calls_requested": tc_names,
                    },
                    output_preview=content,
                )

        if (not tool_calls or finish_reason == "stop" or not use_tools) and textual_tool_request:
            trace["rejected_final_tool_request"] = True
            trace["rejected_final_tool_request_preview"] = content[:500]
            final_repair_attempts += 1
            if final_repair_attempts <= 2:
                force_final_next = True
                current_messages.append({
                    "role": "user",
                    "content": (
                        "Der letzte Text war ein Tool-Aufruf. Tool-Aufrufe sind jetzt nicht mehr erlaubt. "
                        "Gib eine normale finale Antwort auf Basis des vorhandenen Kontexts. "
                        "Erwaehne keine TOOL_REQUEST-Bloecke und kein JSON."
                    ),
                })
                if rec:
                    rec.event(
                        f"tool_loop_llm_{llm_call_count}_rejected_tool_request",
                        "Finale Antwort war ein Tool-Aufruf und wird wiederholt",
                        status="running",
                        details={"finish_reason": finish_reason, "preview": content[:500]},
                    )
                continue
            fallback = last_non_tool_content or (
                "Unklar, bitte Kontext pruefen. Das Modell hat statt einer finalen Antwort erneut einen Tool-Aufruf ausgegeben."
            )
            trace["final_finish_reason"] = "rejected_tool_request_fallback"
            return fallback, trace

        if not tool_calls or finish_reason == "stop" or not use_tools:
            trace["final_finish_reason"] = finish_reason
            return content, trace

        # Add assistant message with tool_calls to history
        current_messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        })

        iteration_read_calls = 0
        iteration_search_calls = 0

        for tc in tool_calls:
            if _cancelled():
                return "", trace
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
                iteration_read_calls += 1
                _req_path = str(args.get("path") or "").strip()
                if _req_path in _already_read:
                    result = _already_read[_req_path]
                else:
                    result = _dispatch_tool(
                        fn_name, args,
                        repo_root=repo_root,
                        max_chars_per_file=max_chars_per_file,
                    )
                    if not result.startswith("[Fehler"):
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
                        _already_read[_req_path] = result
                        _remember_file(_req_path, result, source="tool_read")
            elif fn_name == "search_codebase":
                iteration_search_calls += 1
                search_call_count += 1
                _query = str(args.get("query") or "").strip().lower()
                if _query in _already_searched:
                    result = (
                        "[Suche bereits ausgefuehrt. Nutze die bestehende Evidenz, "
                        "lies eine konkrete Datei aus der Trefferliste oder antworte abschliessend.]"
                    )
                    if search_call_count >= 3:
                        force_final_next = True
                elif max_search_calls > 0 and search_call_count > max_search_calls:
                    result = (
                        "[Suchlimit erreicht. Nutze die vorhandene Dateiliste und Evidenz; "
                        "lies bei Bedarf eine konkrete Datei oder antworte abschliessend.]"
                    )
                    force_final_next = True
                else:
                    _already_searched.add(_query)
                    result = _dispatch_tool(
                        fn_name, args,
                        repo_root=repo_root,
                        max_chars_per_file=max_chars_per_file,
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

        _compact_initial_packed_context()
        evidence_text = _evidence_prompt()
        if evidence_text and tool_calls and tool_call_count < max_tool_calls:
            _replace_or_append_evidence_message(evidence_text)
            if rec:
                rec.event(
                    "tool_loop_evidence_memory",
                    f"Recherche-Stand aktualisiert ({len(_evidence)} Datei(en))",
                    status="completed",
                    details={"files": list(_evidence.keys())},
                    input_preview=evidence_text,
                )

        if (
            iteration_search_calls
            and not iteration_read_calls
            and max_search_calls > 0
            and search_call_count >= max_search_calls
        ):
            force_final_next = True

        if max_tool_calls > 0 and tool_call_count >= max_tool_calls:
            _replace_or_append_evidence_message(
                _evidence_prompt()
                + "\n\nBitte gib jetzt deine abschliessende Antwort auf Basis aller gesammelten Informationen."
            )

    return last_content, trace
