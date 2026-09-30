"""Tool-call execution of the rag_iterative tool loop.

Executes the native (OpenAI-style) and the textual fallback tool calls of
one loop iteration against the repository tools, with duplicate blocking,
search budgets and the automatic CodeCompass symbol companion.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from agent.routes.snakes_rag_tool_loop_state import (
    EvidenceMemory,
    RagToolLoopOptions,
    RagToolLoopState,
)
from agent.routes.snakes_rag_tools import _CODECOMPASS_CHAT_TOOL_MAP

_log = logging.getLogger("agent.routes.snakes_rag_tool_loop")

_SEARCH_REPEATED = (
    "[Suche bereits ausgefuehrt. Nutze die bestehende Evidenz, "
    "lies eine konkrete Datei aus der Trefferliste oder antworte abschliessend.]"
)
_SEARCH_LIMIT_REACHED = (
    "[Suchlimit erreicht. Nutze die vorhandene Dateiliste und Evidenz; "
    "lies bei Bedarf eine konkrete Datei oder antworte abschliessend.]"
)
_CODECOMPASS_DUPLICATE = (
    "[Duplikat blockiert: Dieses CodeCompass-Tool wurde mit identischen "
    "Argumenten bereits ausgefuehrt. Nutze die vorhandene Evidenz oder "
    "expandiere einen konkreten Architektur-Handle.]"
)
_CODECOMPASS_EVIDENCE_CHARS = 6000


def _duplicate_read_message(path: str) -> str:
    return (
        f"[Duplikat blockiert: {path} wurde bereits gelesen. "
        "Nutze die vorhandene Evidenz, waehle eine ANDERE Datei oder "
        "beende die Recherche mit einer finalen Antwort.]"
    )


def _call_title(prefix: str, fn_name: str, args: dict[str, Any]) -> str:
    return f"{prefix}: {fn_name}({', '.join(f'{k}={v!r}' for k, v in list(args.items())[:2])})"


def _parsed_arguments(fn: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(str(fn.get("arguments") or "{}"))
    except Exception:
        return {}


class ToolCallExecutor:
    """Run the tool calls requested by the model and record them in the trace."""

    def __init__(
        self,
        options: RagToolLoopOptions,
        state: RagToolLoopState,
        evidence: EvidenceMemory,
        dispatch_tool: Callable[..., str],
    ) -> None:
        self._options = options
        self._state = state
        self._evidence = evidence
        self._dispatch_tool = dispatch_tool

    def _dispatch(self, fn_name: str, args: dict[str, Any]) -> str:
        return self._dispatch_tool(
            fn_name,
            args,
            repo_root=self._options.repo_root,
            max_chars_per_file=self._options.max_chars_per_file,
        )

    # ── individual tools ─────────────────────────────────────────────────────

    def _read_file(self, args: dict[str, Any], *, source: str, traced_summary: bool) -> str:
        state = self._state
        path = str(args.get("path") or "").strip()
        if path in self._evidence.already_read:
            state.record_duplicate_blocked(streak=True)
            result = _duplicate_read_message(path)
        else:
            state.duplicate_call_streak = 0
            result = self._dispatch("read_file", args)
            if not result.startswith("[Fehler"):
                if self._options.summarize_reads:
                    result = self._summarize_read(path, result, traced=traced_summary)
                self._evidence.cache_read_result(path, result, source=source)
        self._evidence.retire_initial_next_step_instruction()
        return result

    def _summarize_read(self, path: str, content: str, *, traced: bool) -> str:
        if not traced:
            return self._evidence.summarize_file(path, content)
        return self._evidence.summarize_traced(
            path,
            content,
            event_id=f"tool_call_{self._state.tool_call_count}_summarize",
            running_title=f"Zusammenfasse: {path}",
            done_title=lambda raw, summary: f"Zusammengefasst: {path} ({raw} → {summary} Zeichen)",
        )

    def _search_codebase(self, args: dict[str, Any], *, force_final_on_repeat: bool) -> str:
        state = self._state
        state.search_call_count += 1
        query = str(args.get("query") or "").strip().lower()
        if query in state.already_searched:
            if force_final_on_repeat and state.search_call_count >= 3:
                state.force_final_next = True
            return _SEARCH_REPEATED
        if state.max_search_calls > 0 and state.search_call_count > state.max_search_calls:
            state.force_final_next = True
            return _SEARCH_LIMIT_REACHED
        state.already_searched.add(query)
        return self._dispatch("search_codebase", args)

    def _codecompass_or_other(self, fn_name: str, args: dict[str, Any], iteration: int) -> str:
        state = self._state
        is_codecompass = fn_name in _CODECOMPASS_CHAT_TOOL_MAP
        call_key = f"{fn_name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}" if is_codecompass else ""
        if call_key and call_key in state.completed_codecompass_calls:
            state.record_duplicate_blocked(streak=False)
            return _CODECOMPASS_DUPLICATE
        if call_key:
            state.completed_codecompass_calls.add(call_key)
        result = self._dispatch(fn_name, args)
        if is_codecompass:
            state.codecompass_evidence.append(f"[{fn_name}]\n{result[:_CODECOMPASS_EVIDENCE_CHARS]}")
        if fn_name == "codecompass_architecture_overview":
            result += self._symbol_context_companion(args, iteration)
        return result

    def _symbol_context_companion(self, args: dict[str, Any], iteration: int) -> str:
        """Automatically pair an architecture overview with ranked symbol context."""
        state = self._state
        query = str(args.get("query") or self._options.question)
        symbol_result = self._dispatch(
            "codecompass_symbol_context",
            {
                "query": query,
                "ranked_sources": [
                    {"source": path, "score": 100 - index}
                    for index, path in enumerate(self._options.initial_files or [])
                ],
            },
        )
        state.tool_call_count += 1
        state.trace["tools_used"].append({
            "iteration": iteration,
            "name": "codecompass_symbol_context",
            "args": {"query": query[:120]},
            "result_chars": len(symbol_result),
            "automatic_companion": True,
        })
        state.codecompass_evidence.append(
            "[codecompass_symbol_context]\n" + symbol_result[:_CODECOMPASS_EVIDENCE_CHARS]
        )
        return "\n\n[AUTOMATISCHE CODECOMPASS-SYMBOL-EVIDENZ]\n" + symbol_result[:_CODECOMPASS_EVIDENCE_CHARS]

    def _record_call(self, fn_name: str, args: dict[str, Any], result: str, iteration: int, *, textual: bool) -> None:
        state = self._state
        used: dict[str, Any] = {
            "iteration": iteration,
            "name": fn_name,
            "args": {k: str(v)[:120] for k, v in args.items()},
            "result_chars": len(result),
        }
        details: dict[str, Any] = {"function": fn_name, "args": args, "result_chars": len(result)}
        if textual:
            used["source"] = "textual"
            details["source"] = "textual"
        state.trace["tools_used"].append(used)
        state.trace["tool_calls_made"] = state.tool_call_count
        if self._options.rec:
            self._options.rec.event(
                f"tool_call_{state.tool_call_count}",
                _call_title("Tool (textuell)" if textual else "Tool", fn_name, args),
                status="completed",
                details=details,
                output_preview=result[:500] if result else None,
            )

    # ── one iteration ────────────────────────────────────────────────────────

    def run_textual_calls(self, parsed_calls: list[dict[str, Any]], iteration: int) -> list[str] | None:
        """Execute textual fallback calls; ``None`` when the run was cancelled."""
        state = self._state
        result_parts: list[str] = []
        for call in parsed_calls:
            if state.cancelled(self._options.cancel_event):
                return None
            state.tool_call_count += 1
            fn_name = call["name"]
            args = call["args"]
            if fn_name == "read_file":
                result = self._read_file(args, source="textual_read", traced_summary=False)
            elif fn_name == "search_codebase":
                result = self._search_codebase(args, force_final_on_repeat=False)
            else:
                result = self._dispatch(fn_name, args)
            self._record_call(fn_name, args, result, iteration, textual=True)
            first_arg = str(list(args.values())[0])[:80] if args else ""
            result_parts.append(f"[Tool-Ergebnis: {fn_name}({first_arg!r})]\n{result}\n[/Tool-Ergebnis]")
        return result_parts

    def run_native_calls(self, tool_calls: list[dict[str, Any]], iteration: int) -> tuple[int, int] | None:
        """Execute native tool calls; returns ``(read_calls, search_calls)`` or ``None`` when cancelled."""
        state = self._state
        read_calls = 0
        search_calls = 0
        for tc in tool_calls:
            if state.cancelled(self._options.cancel_event):
                return None
            state.tool_call_count += 1
            tc_id = str(tc.get("id") or f"call_{state.tool_call_count}")
            fn = tc.get("function") or {}
            fn_name = str(fn.get("name") or "")
            args = _parsed_arguments(fn)
            _log.debug("tool_loop: calling %s(%s)", fn_name, args)
            if fn_name == "read_file":
                read_calls += 1
                result = self._read_file(args, source="tool_read", traced_summary=True)
            elif fn_name == "search_codebase":
                search_calls += 1
                result = self._search_codebase(args, force_final_on_repeat=True)
            else:
                result = self._codecompass_or_other(fn_name, args, iteration)
            self._record_call(fn_name, args, result, iteration, textual=False)
            state.messages.append({"role": "tool", "tool_call_id": tc_id, "content": result})
        return read_calls, search_calls
