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

from agent.routes import snakes_rag_text_protocol as _text_protocol
from agent.routes import snakes_rag_tools as _rag_tools
from agent.routes.snakes_rag_synthesis import build_synthesis_prompt
from agent.services.snake_chat_cancellation import is_chat_cancelled
from agent.utils import log_llm_entry

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

_UNLIMITED_TOOL_LOOP_MAX_ITERATIONS = 24

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
    """
    import requests

    from agent.llm_integration import _runtime_api_key, _runtime_provider_urls
    from agent.routes.snakes_worker_routing import (
        _worker_profile_chat,
        snake_profile_routing_enabled,
    )

    # 0 = truly unlimited; the loop still exits when the model stops calling tools
    _effective_max = max_tool_calls if max_tool_calls > 0 else 0
    max_tool_calls = _effective_max

    trace: dict[str, Any] = {
        "mode": "tool_loop",
        "tool_calls_made": 0,
        "textual_tool_calls_detected": 0,
        "tools_used": [],
        "evidence": [],
        "max_tool_calls_effective": max_tool_calls if max_tool_calls > 0 else "unlimited",
        "max_search_calls_effective": max_search_calls if max_search_calls > 0 else "unlimited",
    }

    urls = _runtime_provider_urls()
    base_url = str(api_base or urls.get(provider) or "").rstrip("/")
    api_key = _runtime_api_key(provider)

    use_profile_routing = snake_profile_routing_enabled()
    trace["inference_route"] = "hub_worker_local_profiles" if use_profile_routing else "legacy_direct_provider"

    if not base_url and not use_profile_routing:
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
    duplicate_call_streak = 0
    duplicate_calls_blocked = 0
    _codecompass_evidence: list[str] = []
    _completed_codecompass_calls: set[str] = set()

    def _cancelled() -> bool:
        if not is_chat_cancelled(cancel_event):
            return False
        trace["cancelled"] = True
        trace["error"] = "cancelled"
        return True

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
            if use_profile_routing:
                routed, routed_trace = _worker_profile_chat(
                    [{"role": "user", "content": summary_prompt}],
                    task_kind="summarization",
                )
                trace.setdefault("worker_routes", []).append(routed_trace)
                if not routed:
                    raise RuntimeError(routed_trace.get("error") or "worker_profile_summary_failed")
                response_data = routed
            else:
                import requests as _req
                resp = _req.post(
                    endpoint,
                    json={"model": model or "auto", "messages": [{"role": "user", "content": summary_prompt}]},
                    headers=headers,
                    timeout=min(timeout, 120),
                )
                resp.raise_for_status()
                response_data = resp.json()
            if _cancelled():
                return "[Abgebrochen]"
            summary = str(
                ((response_data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
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

    def _cache_read_result(requested_path: str, result: str, *, source: str) -> None:
        _already_read[requested_path] = result
        remembered_path = requested_path
        corrected = re.match(r"^\[Pfad automatisch korrigiert: .*? -> ([^\]]+)\]", result)
        if corrected:
            remembered_path = corrected.group(1).strip()
            _already_read[remembered_path] = result
        _remember_file(remembered_path, result, source=source)

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
        return format_evidence_prompt(_evidence, question)

    def _compact_initial_packed_context() -> None:
        if not _evidence:
            return
        if compact_initial_packed_context(current_messages):
            trace["initial_context_compacted_for_followups"] = True

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

    def _retire_initial_next_step_instruction() -> None:
        if retire_initial_next_step_instruction(current_messages):
            trace["initial_next_step_instruction_retired"] = True

    def _prepare_profile_final_synthesis_context(*, retry: bool = False) -> None:
        synthesis_prompt = build_synthesis_prompt(
            question=question,
            architecture_context=architecture_context,
            codecompass_evidence=_codecompass_evidence,
            evidence=_evidence_prompt(),
            research_hint=last_non_tool_content,
            retry=retry,
        )
        current_messages[:] = [{"role": "user", "content": synthesis_prompt}]
        trace["final_synthesis_context_chars"] = len(synthesis_prompt)
        trace["final_synthesis_context_mode"] = "retry_compact" if retry else "bounded"

    def _retry_profile_final_synthesis() -> tuple[dict[str, Any] | None, dict[str, Any]]:
        _prepare_profile_final_synthesis_context(retry=True)
        try:
            routed, retry_trace = _worker_profile_chat(
                current_messages,
                task_kind=final_task_kind,
                tools=None,
                timeout_seconds=min(360, max(300, timeout)),
            )
        except Exception as exc:
            routed = None
            retry_trace = {
                "routing_task_kind": final_task_kind,
                "routing_source": "hub_snake_profile_policy",
                "timeout_seconds": min(360, max(300, timeout)),
                "error": str(exc)[:200],
            }
        trace.setdefault("worker_routes", []).append(retry_trace)
        trace["final_synthesis_retry_attempted"] = True
        return routed, retry_trace

    _register_initial_evidence()
    _compact_initial_packed_context()
    _replace_or_append_evidence_message(_evidence_prompt())

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

    # ``max_tool_calls == 0`` means no configured tool-call limit. Keep a
    # defensive iteration cap so ambiguous-path hints can be resolved without
    # risking an infinite LLM/tool loop.
    _max_iterations = max_tool_calls + 2 if max_tool_calls > 0 else _UNLIMITED_TOOL_LOOP_MAX_ITERATIONS
    for _iteration in range(_max_iterations + 1):
        if _cancelled():
            if rec:
                rec.event(
                    "tool_loop_cancelled",
                    "Tool-Loop abgebrochen",
                    status="cancelled",
                    details=trace,
                )
            return "", trace

        if config_provider is not None and not lock_tool_budgets:
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

        if _iteration == _max_iterations:
            force_final_next = True
            trace["forced_final_reason"] = "defensive_iteration_cap"

        search_only_exhausted = (
            max_search_calls > 0
            and search_call_count >= max_search_calls
            and not any(item.get("name") == "read_file" for item in trace.get("tools_used", []))
        )
        if search_only_exhausted:
            force_final_next = True
        if force_final_next and use_profile_routing:
            _prepare_profile_final_synthesis_context()
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
            if use_profile_routing:
                # LFM performs the bounded tool decision; KAT handles the
                # tool-free repository synthesis. Both execute on a worker.
                routed_kind = "classification" if use_tools else final_task_kind
                data, routed_trace = _worker_profile_chat(
                    current_messages,
                    task_kind=routed_kind,
                    tools=_CHAT_TOOLS if use_tools else None,
                    timeout_seconds=(
                        min(360, max(300, timeout)) if not use_tools else min(90, timeout)
                    ),
                )
                trace.setdefault("worker_routes", []).append(routed_trace)
                if not data:
                    raise RuntimeError(routed_trace.get("error") or "worker_profile_chat_failed")
            else:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
            if _cancelled():
                if rec:
                    rec.event(
                        f"tool_loop_llm_{llm_call_count}_cancelled",
                        f"{label} — abgebrochen",
                        status="cancelled",
                    )
                return "", trace
        except Exception as exc:
            _log.warning("tool_loop: LLM call failed: %s", exc)
            if use_profile_routing and not use_tools and last_non_tool_content:
                data, routed_trace = _retry_profile_final_synthesis()
                if data:
                    trace["final_synthesis_primary_error"] = str(exc)[:200]
                else:
                    trace["final_synthesis_status"] = "completed_degraded"
                    trace["final_synthesis_error"] = (
                        routed_trace.get("error") or str(exc)[:200]
                    )
                    trace["fallback_answer_source"] = "research_answer"
                    return last_non_tool_content, trace
            else:
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
        if use_profile_routing and not use_tools and not content and last_non_tool_content:
            retry_data, retry_trace = _retry_profile_final_synthesis()
            if retry_data:
                retry_choice = (retry_data.get("choices") or [{}])[0]
                retry_msg = retry_choice.get("message") or {}
                content = str(retry_msg.get("content") or "").strip()
                tool_calls = list(retry_msg.get("tool_calls") or [])
                finish_reason = str(retry_choice.get("finish_reason") or finish_reason)
                routed_trace = retry_trace
            if not content:
                trace["final_synthesis_status"] = "completed_degraded"
                trace["final_synthesis_error"] = (
                    retry_trace.get("error") or "empty_final_synthesis"
                )
                trace["fallback_answer_source"] = "research_answer"
                return last_non_tool_content, trace
        if use_profile_routing and not use_tools:
            trace["final_synthesis_status"] = "completed"
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
                    "runtime_inference": routed_trace.get("inference") if use_profile_routing else None,
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
            trace["textual_tool_calls_detected"] = trace.get("textual_tool_calls_detected", 0) + 1

            # Try to parse and execute the textual tool calls (fallback for models without
            # native function-calling support, e.g. phi-3.5-mini).
            parsed_calls = _parse_textual_tool_calls(content) if use_tools else []

            if parsed_calls:
                current_messages.append({"role": "assistant", "content": content})
                result_parts: list[str] = []
                for call in parsed_calls:
                    if _cancelled():
                        return "", trace
                    tool_call_count += 1
                    fn_name = call["name"]
                    args = call["args"]

                    if fn_name == "read_file":
                        _req_path = str(args.get("path") or "").strip()
                        if _req_path in _already_read:
                            duplicate_calls_blocked += 1
                            duplicate_call_streak += 1
                            trace["duplicate_calls_blocked"] = duplicate_calls_blocked
                            result = (
                                f"[Duplikat blockiert: {_req_path} wurde bereits gelesen. "
                                "Nutze die vorhandene Evidenz, waehle eine ANDERE Datei oder "
                                "beende die Recherche mit einer finalen Antwort.]"
                            )
                        else:
                            duplicate_call_streak = 0
                            result = _dispatch_tool(
                                fn_name, args, repo_root=repo_root, max_chars_per_file=max_chars_per_file
                            )
                            if not result.startswith("[Fehler"):
                                if summarize_reads:
                                    result = _summarize_file(_req_path, result)
                                _cache_read_result(_req_path, result, source="textual_read")
                        _retire_initial_next_step_instruction()
                    elif fn_name == "search_codebase":
                        search_call_count += 1
                        _query = str(args.get("query") or "").strip().lower()
                        if _query in _already_searched:
                            result = (
                                "[Suche bereits ausgefuehrt. Nutze die bestehende Evidenz, "
                                "lies eine konkrete Datei aus der Trefferliste oder antworte abschliessend.]"
                            )
                        elif max_search_calls > 0 and search_call_count > max_search_calls:
                            result = (
                                "[Suchlimit erreicht. Nutze die vorhandene Dateiliste und Evidenz; "
                                "lies bei Bedarf eine konkrete Datei oder antworte abschliessend.]"
                            )
                            force_final_next = True
                        else:
                            _already_searched.add(_query)
                            result = _dispatch_tool(
                                fn_name, args, repo_root=repo_root, max_chars_per_file=max_chars_per_file
                            )
                    else:
                        result = _dispatch_tool(
                            fn_name, args, repo_root=repo_root, max_chars_per_file=max_chars_per_file
                        )

                    trace["tools_used"].append({
                        "iteration": _iteration,
                        "name": fn_name,
                        "args": {k: str(v)[:120] for k, v in args.items()},
                        "result_chars": len(result),
                        "source": "textual",
                    })
                    trace["tool_calls_made"] = tool_call_count

                    first_arg = str(list(args.values())[0])[:80] if args else ""
                    result_parts.append(
                        f"[Tool-Ergebnis: {fn_name}({first_arg!r})]\n{result}\n[/Tool-Ergebnis]"
                    )

                    if rec:
                        rec.event(
                            f"tool_call_{tool_call_count}",
                            f"Tool (textuell): {fn_name}({', '.join(f'{k}={v!r}' for k, v in list(args.items())[:2])})",
                            status="completed",
                            details={
                                "function": fn_name,
                                "args": args,
                                "result_chars": len(result),
                                "source": "textual",
                            },
                            output_preview=result[:500] if result else None,
                        )

                current_messages.append({
                    "role": "user",
                    "content": (
                        "\n\n".join(result_parts)
                        + "\n\nBitte beantworte jetzt die Frage auf Basis dieser Ergebnisse "
                        "und des vorhandenen Kontexts."
                    ),
                })
                _compact_initial_packed_context()
                evidence_text = _evidence_prompt()
                if evidence_text:
                    _replace_or_append_evidence_message(evidence_text)
                if duplicate_call_streak >= 2:
                    force_final_next = True
                    trace["forced_final_reason"] = "repeated_duplicate_tool_call"
                    trace["duplicate_calls_blocked"] = duplicate_calls_blocked
                continue

            # No parseable calls or use_tools=False — single repair attempt, then bail
            trace["rejected_final_tool_request"] = True
            trace["rejected_final_tool_request_preview"] = content[:500]
            final_repair_attempts += 1
            if final_repair_attempts <= 1:
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
                "Unklar, bitte Kontext pruefen. Das Modell hat statt einer finalen Antwort "
                "erneut einen Tool-Aufruf ausgegeben."
            )
            trace["final_finish_reason"] = "rejected_tool_request_fallback"
            return fallback, trace

        if use_profile_routing and use_tools and (
            not tool_calls or finish_reason == "stop"
        ):
            force_final_next = True
            trace["forced_final_reason"] = "research_complete"
            if content:
                current_messages.append({
                    "role": "assistant",
                    "content": "[LFM-Recherchehinweis]\n" + content,
                })
            _replace_or_append_evidence_message(
                _evidence_prompt()
                + "\n\nDie Recherchephase ist abgeschlossen. Erzeuge jetzt als Coding-Modell "
                "die verbindliche finale Antwort aus der gesammelten Evidenz."
            )
            if rec:
                rec.event(
                    "tool_loop_research_complete_handoff",
                    "LFM-Recherche abgeschlossen — Übergabe an KAT-Synthese",
                    status="completed",
                    details={
                        "research_model": (routed_trace.get("inference") or {}).get("model"),
                        "next_task_kind": final_task_kind,
                    },
                )
            continue

        if not tool_calls or finish_reason == "stop" or not use_tools:
            trace["final_finish_reason"] = finish_reason
            return content or last_non_tool_content, trace

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
                    duplicate_calls_blocked += 1
                    duplicate_call_streak += 1
                    trace["duplicate_calls_blocked"] = duplicate_calls_blocked
                    result = (
                        f"[Duplikat blockiert: {_req_path} wurde bereits gelesen. "
                        "Nutze die vorhandene Evidenz, waehle eine ANDERE Datei oder beende die "
                        "Recherche mit einer finalen Antwort.]"
                    )
                else:
                    duplicate_call_streak = 0
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
                        _cache_read_result(_req_path, result, source="tool_read")
                _retire_initial_next_step_instruction()
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
                codecompass_call_key = (
                    f"{fn_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
                    if fn_name in _CODECOMPASS_CHAT_TOOL_MAP
                    else ""
                )
                duplicate_codecompass_call = bool(
                    codecompass_call_key
                    and codecompass_call_key in _completed_codecompass_calls
                )
                if duplicate_codecompass_call:
                    duplicate_calls_blocked += 1
                    trace["duplicate_calls_blocked"] = duplicate_calls_blocked
                    result = (
                        "[Duplikat blockiert: Dieses CodeCompass-Tool wurde mit identischen "
                        "Argumenten bereits ausgefuehrt. Nutze die vorhandene Evidenz oder "
                        "expandiere einen konkreten Architektur-Handle.]"
                    )
                else:
                    if codecompass_call_key:
                        _completed_codecompass_calls.add(codecompass_call_key)
                    result = _dispatch_tool(
                        fn_name, args,
                        repo_root=repo_root,
                        max_chars_per_file=max_chars_per_file,
                    )
                if fn_name in _CODECOMPASS_CHAT_TOOL_MAP and not duplicate_codecompass_call:
                    _codecompass_evidence.append(
                        f"[{fn_name}]\n{result[:6000]}"
                    )
                if fn_name == "codecompass_architecture_overview" and not duplicate_codecompass_call:
                    symbol_result = _dispatch_tool(
                        "codecompass_symbol_context",
                        {
                            "query": str(args.get("query") or question),
                            "ranked_sources": [
                                {"source": path, "score": 100 - index}
                                for index, path in enumerate(initial_files or [])
                            ],
                        },
                        repo_root=repo_root,
                        max_chars_per_file=max_chars_per_file,
                    )
                    tool_call_count += 1
                    trace["tools_used"].append({
                        "iteration": _iteration,
                        "name": "codecompass_symbol_context",
                        "args": {"query": str(args.get("query") or question)[:120]},
                        "result_chars": len(symbol_result),
                        "automatic_companion": True,
                    })
                    _codecompass_evidence.append(
                        "[codecompass_symbol_context]\n" + symbol_result[:6000]
                    )
                    result += (
                        "\n\n[AUTOMATISCHE CODECOMPASS-SYMBOL-EVIDENZ]\n"
                        + symbol_result[:6000]
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
        if evidence_text and tool_calls and (
            max_tool_calls == 0 or tool_call_count < max_tool_calls
        ):
            _replace_or_append_evidence_message(evidence_text)
            if rec:
                rec.event(
                    "tool_loop_evidence_memory",
                    f"Recherche-Stand aktualisiert ({len(_evidence)} Datei(en))",
                    status="completed",
                    details={"files": list(_evidence.keys())},
                    input_preview=evidence_text,
                )

        if duplicate_call_streak >= 2:
            force_final_next = True
            trace["forced_final_reason"] = "repeated_duplicate_tool_call"
            trace["duplicate_calls_blocked"] = duplicate_calls_blocked
            _replace_or_append_evidence_message(
                _evidence_prompt()
                + "\n\nDie Recherchephase ist beendet, weil derselbe Tool-Aufruf wiederholt wurde. "
                "Erzeuge jetzt zwingend eine normale abschliessende Antwort aus der vorhandenen Evidenz."
            )
            if rec:
                rec.event(
                    "tool_loop_repeated_duplicate_handoff",
                    "Wiederholtes Duplikat blockiert — Übergabe an finale Synthese",
                    status="completed",
                    details={
                        "duplicate_calls_blocked": duplicate_calls_blocked,
                        "next_task_kind": final_task_kind if use_profile_routing else "legacy_final",
                    },
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
