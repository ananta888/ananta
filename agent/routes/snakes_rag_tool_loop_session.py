"""Iteration control of the rag_iterative tool loop.

:class:`RagToolLoopSession` runs the loop of :func:`agent.routes.
snakes_rag_tool_loop.run_rag_chat_tool_loop`: one LLM call per iteration,
then either the final answer, a hand-off to the final synthesis, a repair of
a textual tool request or the execution of the requested tool calls.
"""

from __future__ import annotations

import json
import logging
import pathlib as _pl
from dataclasses import dataclass
from typing import Any

from agent.routes.snakes_rag_text_protocol import (
    full_prompt,
    input_preview,
    looks_like_tool_request,
    parse_file_sections,
    parse_textual_tool_calls,
    total_context_chars,
)
from agent.routes.snakes_rag_tool_loop_state import (
    EvidenceMemory,
    RagLlmTransport,
    RagToolLoopOptions,
    RagToolLoopState,
    budget_label,
)
from agent.routes.snakes_rag_tool_loop_tools import ToolCallExecutor
from agent.routes.snakes_rag_tools import _CHAT_TOOLS
from agent.utils import log_llm_entry

_log = logging.getLogger("agent.routes.snakes_rag_tool_loop")

UNLIMITED_TOOL_LOOP_MAX_ITERATIONS = 24

_REPAIR_TOOL_REQUEST = (
    "Der letzte Text war ein Tool-Aufruf. Tool-Aufrufe sind jetzt nicht mehr erlaubt. "
    "Gib eine normale finale Antwort auf Basis des vorhandenen Kontexts. "
    "Erwaehne keine TOOL_REQUEST-Bloecke und kein JSON."
)
_REPEATED_TOOL_REQUEST_FALLBACK = (
    "Unklar, bitte Kontext pruefen. Das Modell hat statt einer finalen Antwort "
    "erneut einen Tool-Aufruf ausgegeben."
)


@dataclass
class _LlmReply:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str


class _Finished(Exception):
    """Ends the loop with ``answer`` (control flow inside the session only)."""

    def __init__(self, answer: str) -> None:
        super().__init__("tool loop finished")
        self.answer = answer


def _tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    return [str((tc.get("function") or {}).get("name") or "?") for tc in tool_calls]


def _tool_call_details(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        raw_args = str(fn.get("arguments") or "{}")
        try:
            parsed_args = json.loads(raw_args)
        except Exception:
            parsed_args = {"_raw": raw_args}
        details.append({
            "id": str(tc.get("id") or ""),
            "name": str(fn.get("name") or "?"),
            "arguments": parsed_args,
            "raw_arguments": raw_args[:2000],
        })
    return details


class RagToolLoopSession:
    """One run of the agentic tool loop."""

    def __init__(
        self,
        options: RagToolLoopOptions,
        state: RagToolLoopState,
        transport: RagLlmTransport,
        evidence: EvidenceMemory,
        tools: ToolCallExecutor,
    ) -> None:
        self._options = options
        self._state = state
        self._transport = transport
        self._evidence = evidence
        self._tools = tools
        self._routed_trace: dict[str, Any] = {}

    def _event(self, *args, **kwargs) -> None:
        if self._options.rec:
            self._options.rec.event(*args, **kwargs)

    def _cancelled(self) -> bool:
        return self._state.cancelled(self._options.cancel_event)

    # ── entry ────────────────────────────────────────────────────────────────

    def run(self) -> tuple[str, dict[str, Any]]:
        state = self._state
        self._evidence.register_initial_evidence()
        self._evidence.compact_initial_packed_context()
        self._evidence.replace_or_append_message(self._evidence.prompt())
        self._log_initial_context()
        # ``max_tool_calls == 0`` means no configured tool-call limit. Keep a
        # defensive iteration cap so ambiguous-path hints can be resolved without
        # risking an infinite LLM/tool loop.
        max_iterations = (
            state.max_tool_calls + 2 if state.max_tool_calls > 0 else UNLIMITED_TOOL_LOOP_MAX_ITERATIONS
        )
        try:
            for iteration in range(max_iterations + 1):
                self._iterate(iteration, max_iterations)
        except _Finished as finished:
            return finished.answer, state.trace
        return state.last_content, state.trace

    def _log_initial_context(self) -> None:
        options = self._options
        initial_user_content = str((self._state.messages[-1] or {}).get("content") or "")
        context_chars = total_context_chars(self._state.messages)
        file_sections = parse_file_sections(initial_user_content) if options.initial_files else []
        # Write full initial context to dump file (overwrites each run for easy inspection)
        try:
            from agent.utils import get_data_dir
            (_pl.Path(get_data_dir()) / "last_llm_context.txt").write_text(initial_user_content, encoding="utf-8")
        except Exception as _dump_exc:
            _log.debug("context dump failed: %s", _dump_exc)
        log_llm_entry(
            event="tool_loop_context_summary",
            provider=options.provider,
            model=options.model_label,
            total_context_chars=context_chars,
            initial_files_count=len(options.initial_files or []),
            file_sections=file_sections,
        )
        self._event(
            "tool_loop_initial_context",
            f"Initialer Kontext: {len(options.initial_files or [])} Dateien, {context_chars:,} Zeichen",
            status="info",
            details={
                "files": file_sections,
                "total_context_chars": context_chars,
                "context_dump": "data/last_llm_context.txt",
            },
            input_preview="\n".join(
                "{}.  {}  (relevanz: {})".format(i, s["path"], s.get("score", s.get("chars", "?")))
                for i, s in enumerate(file_sections, 1)
            ) or "(keine Dateien)",
        )

    # ── one iteration ────────────────────────────────────────────────────────

    def _iterate(self, iteration: int, max_iterations: int) -> None:
        state = self._state
        if self._cancelled():
            self._event("tool_loop_cancelled", "Tool-Loop abgebrochen", status="cancelled", details=state.trace)
            raise _Finished("")
        self._refresh_tool_budgets()
        if iteration == max_iterations:
            state.force_final_next = True
            state.trace["forced_final_reason"] = "defensive_iteration_cap"
        if self._search_only_exhausted():
            state.force_final_next = True
        if state.force_final_next and self._transport.use_profile_routing:
            self._evidence.prepare_final_synthesis_context()
        use_tools = state.tool_budget_left() and not state.force_final_next
        state.llm_call_count += 1
        label = self._call_label(use_tools)
        self._log_call_start(iteration, use_tools, label)
        reply = self._reply(self._call_llm(use_tools, label), use_tools)
        textual_tool_request = self._record_reply(reply, use_tools, label)
        if (not reply.tool_calls or reply.finish_reason == "stop" or not use_tools) and textual_tool_request:
            self._handle_textual_tool_request(reply, use_tools, iteration)
            return
        if self._transport.use_profile_routing and use_tools and (
            not reply.tool_calls or reply.finish_reason == "stop"
        ):
            self._hand_off_to_final_synthesis(reply.content)
            return
        if not reply.tool_calls or reply.finish_reason == "stop" or not use_tools:
            state.trace["final_finish_reason"] = reply.finish_reason
            raise _Finished(reply.content or state.last_non_tool_content)
        self._run_native_tool_calls(reply, iteration)

    def _refresh_tool_budgets(self) -> None:
        options = self._options
        state = self._state
        if options.config_provider is None or options.lock_tool_budgets:
            return
        try:
            live = options.config_provider()
            new_max_tool_calls = max(0, int(live.get("rag_iterative_max_tool_calls") or 0))
            new_max_search_calls = max(0, int(live.get("rag_iterative_max_search_calls") or 0))
            if new_max_tool_calls != state.max_tool_calls:
                state.max_tool_calls = new_max_tool_calls
                state.trace["max_tool_calls_effective"] = budget_label(new_max_tool_calls)
            if new_max_search_calls != state.max_search_calls:
                state.max_search_calls = new_max_search_calls
                state.trace["max_search_calls_effective"] = budget_label(new_max_search_calls)
        except Exception:
            pass

    def _search_only_exhausted(self) -> bool:
        state = self._state
        return (
            state.max_search_calls > 0
            and state.search_call_count >= state.max_search_calls
            and not any(item.get("name") == "read_file" for item in state.trace.get("tools_used", []))
        )

    def _call_label(self, use_tools: bool) -> str:
        messages = self._state.messages
        context_chars = total_context_chars(messages)
        count = self._state.llm_call_count
        if use_tools:
            return f"LLM-Call {count} (Tool-Loop, {len(messages)} Msgs, ~{context_chars//1000}K Zeichen)"
        return f"LLM-Call {count} (Finale Antwort, ~{context_chars//1000}K Zeichen)"

    def _log_call_start(self, iteration: int, use_tools: bool, label: str) -> None:
        options = self._options
        state = self._state
        messages = state.messages
        context_chars = total_context_chars(messages)
        registered_tools = [t["function"]["name"] for t in _CHAT_TOOLS] if use_tools else []
        # Show ALL messages exactly as sent to the LLM (each capped at 10K chars)
        self._event(
            f"tool_loop_llm_{state.llm_call_count}",
            label,
            status="running",
            details={
                "iteration": iteration,
                "messages": len(messages),
                "use_tools": use_tools,
                "tool_call_mode": "native_api" if use_tools else "disabled",
                "registered_tools": registered_tools,
                "context_chars": context_chars,
            },
            input_preview=full_prompt(messages),
        )
        log_kwargs: dict[str, Any] = dict(
            event="llm_call_start",
            provider=options.provider,
            model=options.model_label,
            prompt=input_preview(messages),
            tool_loop_call=state.llm_call_count,
            history_len=len(messages),
            context_chars=context_chars,
            tool_call_mode="native_api" if use_tools else "disabled",
            registered_tools=registered_tools,
        )
        if state.llm_call_count == 1 and options.initial_files:
            log_kwargs["initial_files"] = options.initial_files
            log_kwargs["initial_files_count"] = len(options.initial_files)
        log_llm_entry(**log_kwargs)

    # ── LLM call and reply ───────────────────────────────────────────────────

    def _call_llm(self, use_tools: bool, label: str) -> dict[str, Any]:
        try:
            data = self._request_completion(use_tools)
        except Exception as exc:
            _log.warning("tool_loop: LLM call failed: %s", exc)
            return self._recover_failed_call(exc, use_tools, label)
        if self._cancelled():
            self._event(
                f"tool_loop_llm_{self._state.llm_call_count}_cancelled",
                f"{label} — abgebrochen",
                status="cancelled",
            )
            raise _Finished("")
        return data

    def _request_completion(self, use_tools: bool) -> dict[str, Any]:
        options = self._options
        state = self._state
        if self._transport.use_profile_routing:
            # LFM performs the bounded tool decision; KAT handles the
            # tool-free repository synthesis. Both execute on a worker.
            data, self._routed_trace = self._transport.routed_chat(
                state.messages,
                task_kind="classification" if use_tools else options.final_task_kind,
                tools=_CHAT_TOOLS if use_tools else None,
                timeout_seconds=options.final_synthesis_timeout if not use_tools else min(90, options.timeout),
            )
            if not data:
                raise RuntimeError(self._routed_trace.get("error") or "worker_profile_chat_failed")
            return data
        payload: dict[str, Any] = {"model": options.model_label, "messages": state.messages}
        if use_tools:
            payload["tools"] = _CHAT_TOOLS
            payload["tool_choice"] = "auto"
        return self._transport.direct_chat(payload, timeout=options.timeout)

    def _recover_failed_call(self, exc: Exception, use_tools: bool, label: str) -> dict[str, Any]:
        state = self._state
        if self._transport.use_profile_routing and not use_tools and state.last_non_tool_content:
            data, self._routed_trace = self._retry_final_synthesis()
            if data:
                state.trace["final_synthesis_primary_error"] = str(exc)[:200]
                return data
            self._degrade_to_research_answer(self._routed_trace.get("error") or str(exc)[:200])
        state.trace["error"] = f"llm_call_failed: {exc}"
        log_llm_entry(
            event="llm_call_end",
            provider=self._options.provider,
            model=self._options.model_label,
            success=False,
            tool_loop_call=state.llm_call_count,
            response="",
            error=str(exc),
        )
        self._event(
            f"tool_loop_llm_{state.llm_call_count}_done",
            f"{label} — Fehler",
            status="failed",
            details={"error": str(exc)},
        )
        raise _Finished(state.last_content)

    def _retry_final_synthesis(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        options = self._options
        self._evidence.prepare_final_synthesis_context(retry=True)
        try:
            routed, retry_trace = self._transport.routed_chat(
                self._state.messages,
                task_kind=options.final_task_kind,
                tools=None,
                timeout_seconds=options.final_synthesis_timeout,
            )
        except Exception as exc:
            routed = None
            retry_trace = {
                "routing_task_kind": options.final_task_kind,
                "routing_source": "hub_snake_profile_policy",
                "timeout_seconds": options.final_synthesis_timeout,
                "error": str(exc)[:200],
            }
            self._state.trace.setdefault("worker_routes", []).append(retry_trace)
        self._state.trace["final_synthesis_retry_attempted"] = True
        return routed, retry_trace

    def _degrade_to_research_answer(self, error: str) -> None:
        trace = self._state.trace
        trace["final_synthesis_status"] = "completed_degraded"
        trace["final_synthesis_error"] = error
        trace["fallback_answer_source"] = "research_answer"
        raise _Finished(self._state.last_non_tool_content)

    def _reply(self, data: dict[str, Any], use_tools: bool) -> _LlmReply:
        state = self._state
        try:
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            finish_reason = str(choice.get("finish_reason") or "")
        except Exception:
            state.trace["error"] = "invalid_llm_response"
            raise _Finished(state.last_content) from None
        reply = _LlmReply(str(msg.get("content") or "").strip(), list(msg.get("tool_calls") or []), finish_reason)
        profile_final = self._transport.use_profile_routing and not use_tools
        if profile_final and not reply.content and state.last_non_tool_content:
            reply = self._retry_empty_final_synthesis(reply)
        if profile_final:
            state.trace["final_synthesis_status"] = "completed"
        return reply

    def _retry_empty_final_synthesis(self, reply: _LlmReply) -> _LlmReply:
        retry_data, retry_trace = self._retry_final_synthesis()
        if retry_data:
            retry_choice = (retry_data.get("choices") or [{}])[0]
            retry_msg = retry_choice.get("message") or {}
            reply = _LlmReply(
                str(retry_msg.get("content") or "").strip(),
                list(retry_msg.get("tool_calls") or []),
                str(retry_choice.get("finish_reason") or reply.finish_reason),
            )
            self._routed_trace = retry_trace
        if not reply.content:
            self._degrade_to_research_answer(retry_trace.get("error") or "empty_final_synthesis")
        return reply

    def _record_reply(self, reply: _LlmReply, use_tools: bool, label: str) -> bool:
        """Log the reply; returns whether its text is a textual tool request."""
        state = self._state
        state.last_content = reply.content or state.last_content
        textual_tool_request = looks_like_tool_request(reply.content)
        if reply.content and not textual_tool_request:
            state.last_non_tool_content = reply.content
        names = _tool_names(reply.tool_calls)
        log_llm_entry(
            event="llm_call_end",
            provider=self._options.provider,
            model=self._options.model_label,
            success=True,
            tool_loop_call=state.llm_call_count,
            finish_reason=reply.finish_reason,
            response=reply.content[:2000] if reply.content else (f"→ tool_calls: {names}" if names else ""),
            tool_calls=names,
        )
        if self._options.rec:
            self._trace_reply(reply, use_tools, label, names, textual_tool_request)
        return textual_tool_request

    def _trace_reply(self, reply: _LlmReply, use_tools: bool, label: str, names: list[str], textual: bool) -> None:
        count = self._state.llm_call_count
        details = _tool_call_details(reply.tool_calls)
        self._event(
            f"tool_loop_llm_{count}_done",
            f"{label} — {'Tool-Calls: ' + ', '.join(names) if names else 'Antwort erhalten'}",
            status="completed",
            details={
                "finish_reason": reply.finish_reason,
                "tool_calls_requested": names,
                "tool_call_details": details,
                "answer_chars": len(reply.content),
                "runtime_inference": (
                    self._routed_trace.get("inference") if self._transport.use_profile_routing else None
                ),
            },
            output_preview=reply.content if reply.content else (
                "\n".join(f"→ Tool-Call: {item['name']}({item['raw_arguments']})" for item in details)
                if details else None
            ),
        )
        if textual:
            self._event(
                f"tool_loop_llm_{count}_textual_tool_request",
                "Textueller Tool-Request im Modelltext erkannt (Fallback-Pfad)",
                status=(
                    "blocked" if (not use_tools or reply.finish_reason == "stop" or not reply.tool_calls) else "warning"
                ),
                details={
                    "tool_call_mode": "textual_fallback",
                    "finish_reason": reply.finish_reason,
                    "use_tools": use_tools,
                    "tool_calls_requested": names,
                },
                output_preview=reply.content,
            )

    # ── reply handling ───────────────────────────────────────────────────────

    def _handle_textual_tool_request(self, reply: _LlmReply, use_tools: bool, iteration: int) -> None:
        """Execute textual tool calls (models without native function calling) or repair once."""
        state = self._state
        state.trace["textual_tool_calls_detected"] = state.trace.get("textual_tool_calls_detected", 0) + 1
        parsed_calls = parse_textual_tool_calls(reply.content) if use_tools else []
        if parsed_calls:
            state.messages.append({"role": "assistant", "content": reply.content})
            result_parts = self._tools.run_textual_calls(parsed_calls, iteration)
            if result_parts is None:
                raise _Finished("")
            state.messages.append({
                "role": "user",
                "content": (
                    "\n\n".join(result_parts)
                    + "\n\nBitte beantworte jetzt die Frage auf Basis dieser Ergebnisse "
                    "und des vorhandenen Kontexts."
                ),
            })
            self._evidence.compact_initial_packed_context()
            self._evidence.replace_or_append_message(self._evidence.prompt())
            if state.duplicate_call_streak >= 2:
                state.force_final_next = True
                state.trace["forced_final_reason"] = "repeated_duplicate_tool_call"
                state.trace["duplicate_calls_blocked"] = state.duplicate_calls_blocked
            return
        # No parseable calls or use_tools=False — single repair attempt, then bail
        state.trace["rejected_final_tool_request"] = True
        state.trace["rejected_final_tool_request_preview"] = reply.content[:500]
        state.final_repair_attempts += 1
        if state.final_repair_attempts > 1:
            state.trace["final_finish_reason"] = "rejected_tool_request_fallback"
            raise _Finished(state.last_non_tool_content or _REPEATED_TOOL_REQUEST_FALLBACK)
        state.force_final_next = True
        state.messages.append({"role": "user", "content": _REPAIR_TOOL_REQUEST})
        self._event(
            f"tool_loop_llm_{state.llm_call_count}_rejected_tool_request",
            "Finale Antwort war ein Tool-Aufruf und wird wiederholt",
            status="running",
            details={"finish_reason": reply.finish_reason, "preview": reply.content[:500]},
        )

    def _hand_off_to_final_synthesis(self, research_content: str) -> None:
        state = self._state
        state.force_final_next = True
        state.trace["forced_final_reason"] = "research_complete"
        if research_content:
            state.messages.append({"role": "assistant", "content": "[LFM-Recherchehinweis]\n" + research_content})
        self._evidence.replace_or_append_message(
            self._evidence.prompt()
            + "\n\nDie Recherchephase ist abgeschlossen. Erzeuge jetzt als Coding-Modell "
            "die verbindliche finale Antwort aus der gesammelten Evidenz."
        )
        self._event(
            "tool_loop_research_complete_handoff",
            "LFM-Recherche abgeschlossen — Übergabe an KAT-Synthese",
            status="completed",
            details={
                "research_model": (self._routed_trace.get("inference") or {}).get("model"),
                "next_task_kind": self._options.final_task_kind,
            },
        )

    def _run_native_tool_calls(self, reply: _LlmReply, iteration: int) -> None:
        state = self._state
        # Add assistant message with tool_calls to history
        state.messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": reply.tool_calls})
        counts = self._tools.run_native_calls(reply.tool_calls, iteration)
        if counts is None:
            raise _Finished("")
        read_calls, search_calls = counts
        self._update_evidence_memory(reply.tool_calls)
        if state.duplicate_call_streak >= 2:
            self._hand_off_after_repeated_duplicates()
        if search_calls and not read_calls and 0 < state.max_search_calls <= state.search_call_count:
            state.force_final_next = True
        if state.max_tool_calls > 0 and state.tool_call_count >= state.max_tool_calls:
            self._evidence.replace_or_append_message(
                self._evidence.prompt()
                + "\n\nBitte gib jetzt deine abschliessende Antwort auf Basis aller gesammelten Informationen."
            )

    def _update_evidence_memory(self, tool_calls: list[dict[str, Any]]) -> None:
        self._evidence.compact_initial_packed_context()
        evidence_text = self._evidence.prompt()
        if not (evidence_text and tool_calls and self._state.tool_budget_left()):
            return
        self._evidence.replace_or_append_message(evidence_text)
        self._event(
            "tool_loop_evidence_memory",
            f"Recherche-Stand aktualisiert ({len(self._evidence.files)} Datei(en))",
            status="completed",
            details={"files": list(self._evidence.files.keys())},
            input_preview=evidence_text,
        )

    def _hand_off_after_repeated_duplicates(self) -> None:
        state = self._state
        state.force_final_next = True
        state.trace["forced_final_reason"] = "repeated_duplicate_tool_call"
        state.trace["duplicate_calls_blocked"] = state.duplicate_calls_blocked
        self._evidence.replace_or_append_message(
            self._evidence.prompt()
            + "\n\nDie Recherchephase ist beendet, weil derselbe Tool-Aufruf wiederholt wurde. "
            "Erzeuge jetzt zwingend eine normale abschliessende Antwort aus der vorhandenen Evidenz."
        )
        self._event(
            "tool_loop_repeated_duplicate_handoff",
            "Wiederholtes Duplikat blockiert — Übergabe an finale Synthese",
            status="completed",
            details={
                "duplicate_calls_blocked": state.duplicate_calls_blocked,
                "next_task_kind": (
                    self._options.final_task_kind if self._transport.use_profile_routing else "legacy_final"
                ),
            },
        )
