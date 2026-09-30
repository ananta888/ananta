"""Background AI-Snake chat reply pipeline.

``SnakeChatReplyRunner`` produces one room reply for a chat message: it
resolves the session-bound provider, enriches the prompt with UI/settings
context, picks the answer strategy (Ananta-config tool loop, bounded agentic
RAG, full scan or grounded single-shot delegation) and writes the answer plus
trace events. :mod:`.snakes_chat_reply_spawner` owns the thread and the
UI-state store and injects the collaborators here as narrow callables.

Each strategy is one method working on a :class:`_ReplyContext`; ``run``
only orchestrates them (SRP).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from agent.config import settings
from agent.services.snake_chat_cancellation import (
    register_chat_cancel,
    unregister_chat_cancel,
)

from .snakes_chat_helpers import (
    SnakeAskLimits,
    _answer_overflow_policy,
    _build_grounded_snake_prompt,
    _build_room_conversation_history,
    _build_ui_guide,
    _chat_answer_chars_limit,
    _chat_never_truncate_answers,
    _fit_answer_to_chars,
    _read_ananta_settings_summary,
    _should_include_light_ui_context,
    _trace_feature_enabled,
    _with_answer_budget_instruction,
)
from .snakes_chat_reply_trace import (
    config_loaded_details,
    llm_call_started_summary,
    rag_iterative_summary,
    rag_iterative_trace_status,
)
from .snakes_full_scan import worker_chat_full_scan as _worker_chat_full_scan
from .snakes_rag_iterative import worker_chat_rag_iterative as _worker_chat_rag_iterative
from .snakes_worker_routing import (
    resolve_snake_routing_task_kind,
    snake_profile_routing_enabled,
)

_SNAKE_CHAT_PROMPT = (
    "Du bist AI-Snake im Ananta Hub.\n"
    "Regeln (streng):\n"
    "1) Antworte nur auf Basis des Ananta-Kontexts und der Nutzerfrage.\n"
    "2) Erfinde keine Produkte, URLs, Features, Befehle oder Fakten.\n"
    "3) Wenn Informationen fehlen oder unsicher sind, sage explizit: "
    "\"Unklar, bitte Kontext pruefen\".\n"
    "4) Gib keine externen Links aus, ausser der Nutzer hat explizit danach gefragt.\n"
    "5) Halte Antworten kurz, konkret, technisch nutzbar, auf Deutsch.\n"
    "6) Wenn Schrittfolge noetig ist, gib maximal 5 nummerierte Schritte.\n"
)

_ANANTA_SETTINGS_SESSION = "ananta-settings"
_CONCRETE_FACT_TOKENS = (
    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verfügbar"
)
_SETTINGS_PROFILE_CONFIG_OVERRIDES = {
    "chat_architecture_analysis_mode": False,
    "chat_retrieval_profile": "none",
    "chat_use_codecompass": False,
    "chat_code_questions_repo_first": False,
    "chat_include_local_project": False,
}


@dataclass
class _ReplyContext:
    """Per-reply state shared by the answer strategies."""

    prompt: str
    original_prompt: str
    snake_id: str | None
    owner_principal: dict[str, str] | None
    conversation_history: list[dict[str, str]]
    session_id: str = ""
    session_prompt: str | None = None
    is_settings_profile: bool = False
    provider: str = ""
    model: str | None = None
    api_base: str | None = None
    guide_suffix: str = ""
    answer_chars_limit: int = 0
    chat_config: dict[str, Any] = field(default_factory=dict)
    rec: Any = None
    store: Any = None
    trace_id: str | None = None

    def event(self, *args, **kwargs) -> None:
        if self.rec:
            self.rec.event(*args, **kwargs)

    def complete_trace(self) -> None:
        if self.store and self.trace_id:
            self.store.complete_trace(self.trace_id)

    def cancel_keys(self) -> list[str]:
        return ["room"] + ([self.snake_id] if self.snake_id else [])

    def ask_limits(self) -> SnakeAskLimits:
        return SnakeAskLimits(
            answer_chars=self.answer_chars_limit,
            answer_overflow_policy=_answer_overflow_policy(),
            never_truncate_answers=_chat_never_truncate_answers(),
        )


def _http_timeout_seconds() -> int:
    return min(int(getattr(settings, "http_timeout", 120) or 120), 180)


class SnakeChatReplyRunner:
    """Generate and publish one AI-Snake room reply (runs on a worker thread)."""

    def __init__(
        self,
        *,
        ui_state: dict[str, dict],
        resolve_chat_provider: Callable[[dict[str, Any]], tuple[str, str | None, str | None]],
        append_room_message: Callable[..., Any],
        worker_propose: Callable[..., tuple[Any, Any]],
        worker_picker: Callable[..., Any],
        generate_text: Callable[..., Any],
        logger: logging.Logger,
    ) -> None:
        self._ui_state = ui_state
        self._resolve_chat_provider = resolve_chat_provider
        self._append_room_message = append_room_message
        self._worker_propose = worker_propose
        # Passed through unchanged: worker routing compares the picker by
        # identity to decide whether a failed worker may be retried.
        self._worker_picker = worker_picker
        self._generate_text = generate_text
        self._logger = logger

    def run(
        self,
        *,
        prompt: str,
        snake_id: str | None,
        ui_context: dict | None,
        client_session_id: str,
        context_history: list[dict[str, str]] | None,
        session_snapshot: dict[str, Any] | None,
        owner_principal: dict[str, str] | None,
    ) -> None:
        # Handle /guide intent — bypass normal LLM, trigger visual guide
        if prompt.startswith("/guide "):
            self._start_visual_guide(prompt, snake_id, session_snapshot, owner_principal)
            return

        ctx = _ReplyContext(
            prompt=prompt,
            original_prompt=prompt,
            snake_id=snake_id,
            owner_principal=owner_principal,
            conversation_history=[],
        )
        try:
            self._start_trace(ctx, client_session_id)
            ctx.conversation_history = context_history if context_history is not None else (
                _build_room_conversation_history(
                    snake_id=snake_id,
                    current_text=prompt,
                    session_id=client_session_id,
                    owner_principal=owner_principal,
                )
            )
            session_settings = self._bind_session(ctx, session_snapshot, client_session_id)
            self._resolve_provider(ctx, session_settings)
            self._enrich_prompt(ctx, ui_context, session_snapshot or {}, session_settings)
            ctx.answer_chars_limit = _chat_answer_chars_limit()
            if self._answer_with_specialized_strategy(ctx, session_settings):
                return
            self._answer_with_grounded_delegation(ctx)
        except Exception as exc:
            self._logger.warning("ai-snake-chat-reply failed: %s", exc)
            if ctx.rec and ctx.store and ctx.trace_id:
                try:
                    ctx.rec.event("failed", "Fehler bei der Antwortgenerierung", status="failed",
                                  error=str(exc)[:300])
                    ctx.store.complete_trace(ctx.trace_id, status="failed")
                except Exception:
                    pass
            self._append_room_message(
                text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.",
                session_id=ctx.session_id,
                owner_principal=owner_principal,
            )

    # ── setup ────────────────────────────────────────────────────────────────

    def _start_visual_guide(self, prompt, snake_id, session_snapshot, owner_principal) -> None:
        intent = prompt[7:].strip()
        if not intent:
            return
        ui_now = self._ui_state.get(snake_id or "") or {}
        self._append_room_message(
            text=f"Guide wird gestartet: {intent[:100]}…",
            session_id=str((session_snapshot or {}).get("id") or ""),
            owner_principal=owner_principal,
        )
        from agent.services.visual_guide.service import _visual_guide_service as _vgs
        _vgs.handle_manual_guide(
            snake_id=snake_id or "",
            intent=intent,
            snapshot=str(ui_now.get("ui_snapshot") or ""),
            route=str(ui_now.get("route") or ""),
            owner_principal=owner_principal,
        )

    @staticmethod
    def _start_trace(ctx: _ReplyContext, client_session_id: str) -> None:
        if not _trace_feature_enabled():
            return
        from agent.routes.ai_snake_config import _current_config as _trc_cfg
        from agent.routes.ai_snake_trace_store import TraceRecorder, get_trace_store
        max_preview = int(_trc_cfg().get("ai_snake_trace_max_preview_chars") or 200000)
        ctx.store = get_trace_store()
        ctx.trace_id = ctx.store.new_trace(snake_id=ctx.snake_id, session_id=client_session_id or None)
        ctx.rec = TraceRecorder(ctx.store, ctx.trace_id, max_preview_chars=max_preview)
        prompt_preview = ctx.prompt[:120] + ("…" if len(ctx.prompt) > 120 else "")
        ctx.rec.event("request_received", "Anfrage empfangen", status="completed", summary=f"Prompt: {prompt_preview}")

    def _bind_session(self, ctx: _ReplyContext, session_snapshot, client_session_id: str) -> dict[str, Any]:
        """Bind the immutable, request-authorized session snapshot; return its settings.

        Background work consumes only that snapshot and never re-enters global
        chat state or an active-session fallback.
        """
        authorized_session = deepcopy(session_snapshot) if session_snapshot else {}
        ctx.session_id = str(authorized_session.get("id") or "") if authorized_session else ""
        ctx.session_prompt = str(authorized_session.get("system_prompt") or "").strip() or None
        profile_id = str(authorized_session.get("profile_id") or "")
        ctx.is_settings_profile = _ANANTA_SETTINGS_SESSION in {profile_id, ctx.session_id}
        self._logger.info(
            "chat session resolved: active_session_id=%r client_session_id=%r",
            ctx.session_id, client_session_id,
        )
        return dict(authorized_session.get("settings") or {})

    def _resolve_provider(self, ctx: _ReplyContext, session_settings: dict[str, Any]) -> None:
        from agent.routes.ai_snake_config import _current_config as _provider_config
        provider_config = _provider_config()
        if session_settings:
            provider_config = {**provider_config, **session_settings}
        ctx.provider, ctx.model, ctx.api_base = self._resolve_chat_provider(provider_config)
        ctx.event(
            "config_loaded",
            "Angeforderte Session-Konfiguration geladen",
            status="completed",
            details=config_loaded_details(
                provider=ctx.provider,
                model=ctx.model,
                backend=provider_config.get("chat_backend"),
                session_id=ctx.session_id,
                history_messages=len(ctx.conversation_history),
            ),
        )

    def _effective_ui_context(self, ctx: _ReplyContext, ui_context) -> dict:
        return (ui_context or {}) or (self._ui_state.get(ctx.snake_id or "") if ctx.snake_id else {}) or {}

    def _enrich_prompt(self, ctx: _ReplyContext, ui_context, session_snapshot: dict, session_settings: dict) -> None:
        if ctx.is_settings_profile:
            ctx.prompt = self._settings_profile_prompt(ctx, self._effective_ui_context(ctx, ui_context))
            guide = _build_ui_guide(ctx.original_prompt)
            if guide:
                ctx.guide_suffix = f"\n\n__GUIDE__:{json.dumps(guide, ensure_ascii=False)}"
            return
        if _should_include_light_ui_context(
            active_session_id=ctx.session_id,
            active_session_group=str(session_snapshot.get("group") or "").strip(),
            active_session_settings=session_settings,
            prompt=ctx.original_prompt,
        ):
            # Lightweight UI context for sessions that explicitly benefit from UI state.
            light_ui = self._effective_ui_context(ctx, ui_context)
            light_hint = str(light_ui.get("ui_snapshot") or light_ui.get("route") or "").strip() if light_ui else ""
            if light_hint:
                ctx.prompt = f"[UI-Kontext: {light_hint[:100]}]\n\n{ctx.prompt}"

    @staticmethod
    def _settings_profile_prompt(ctx: _ReplyContext, effective_ui: dict) -> str:
        """Ananta-Settings session: enrich the prompt with UI and current settings context."""
        settings_ctx = _read_ananta_settings_summary()
        settings_block = f"[Aktuelle Ananta-Konfiguration]\n{settings_ctx}\n\n[Nutzerfrage]\n{ctx.prompt}"
        if not effective_ui:
            return settings_block
        route = effective_ui.get("route", "?")
        waypoints = ", ".join(effective_ui.get("visible_waypoints") or []) or "(keine)"
        surface = effective_ui.get("active_surface", "")
        snapshot = str(effective_ui.get("ui_snapshot") or "").strip()
        ui_block = (
            "[Aktueller UI-Kontext]\n"
            + (f"UI-Ansicht: {snapshot}\n" if snapshot else f"Route: {route}\n")
            + (f"Surface: {surface}\n" if surface and not snapshot else "")
            + (f"Waypoints: {waypoints}\n" if not snapshot else "")
            + "\n"
        )
        return f"{ui_block}{settings_block}"

    @staticmethod
    def _effective_chat_config(ctx: _ReplyContext, session_settings: dict) -> dict[str, Any]:
        """Global chat config with session overrides.

        For ananta-settings RAG/code analysis is forced off regardless of
        persisted values (legacy sessions may still carry rag_iterative).
        """
        from agent.routes.ai_snake_config import _current_config
        cfg = _current_config()
        if ctx.is_settings_profile:
            return {
                **cfg,
                **_SETTINGS_PROFILE_CONFIG_OVERRIDES,
                **({"chat_answer_chars": 3000} if not cfg.get("chat_answer_chars") else {}),
            }
        if session_settings:
            return {**cfg, **session_settings}
        return cfg

    # ── answer strategies ────────────────────────────────────────────────────

    def _answer_with_specialized_strategy(self, ctx: _ReplyContext, session_settings: dict) -> bool:
        """Config tool loop, bounded agentic RAG or full scan; ``False`` falls back to grounding."""
        try:
            from agent.services.retrieval_profile_service import _is_full_scan_intent
            from agent.services.snake_agentic_tool_policy import (
                resolve_snake_agentic_tool_decision,
            )
            ctx.chat_config = self._effective_chat_config(ctx, session_settings)
            ctx.answer_chars_limit = _chat_answer_chars_limit()
            if ctx.is_settings_profile:
                self._answer_with_config_tool_loop(ctx)
                return True
            tool_decision = resolve_snake_agentic_tool_decision(ctx.prompt, ctx.chat_config)
            if tool_decision.enabled:
                self._answer_with_rag_iterative(ctx, tool_decision)
                return True
            if _is_full_scan_intent(ctx.prompt, "", ctx.chat_config):
                self._answer_with_full_scan(ctx)
                return True
        except Exception as exc:
            self._logger.debug("full_scan check failed, falling back: %s", exc)
        return False

    def _answer_with_config_tool_loop(self, ctx: _ReplyContext) -> None:
        """ananta-settings: dedicated config tool loop (search_ui_docs, read_ananta_config, get_hub_*)."""
        from agent.routes.snakes_ananta_config_tool_loop import run_ananta_config_tool_loop
        ctx.event("ananta_config_tool_loop_start", "Ananta-Konfig Tool-Loop gestartet",
                  status="running", summary="Konfigurations-Guide mit Tool-Calling aktiv")
        started = time.time()
        cancel_keys = ctx.cancel_keys()
        cancel_event = register_chat_cancel(cancel_keys)
        try:
            answer, trace = run_ananta_config_tool_loop(
                messages=[
                    {"role": "system", "content": ctx.session_prompt or _SNAKE_CHAT_PROMPT},
                    *ctx.conversation_history,
                    {"role": "user", "content": ctx.prompt},
                ],
                provider=ctx.provider,
                model=ctx.model,
                api_base=ctx.api_base,
                max_tool_calls=8,
                timeout=120,
                cancel_event=cancel_event,
            )
        finally:
            unregister_chat_cancel(cancel_keys, cancel_event)
        tools_used = ", ".join(trace.get("tools_used") or []) or "–"
        summary = f"ananta-config: {trace.get('tool_calls_made', 0)} Tool-Calls [{tools_used}]"
        ctx.event("ananta_config_tool_loop_done", "Ananta-Konfig Tool-Loop abgeschlossen",
                  status="completed" if answer else "failed",
                  summary=summary,
                  duration_ms=(time.time() - started) * 1000,
                  details=trace)
        answer = answer or "Keine Antwort vom Konfigurations-Guide."
        self._publish(ctx, f"{answer}\n\n[{summary}]{ctx.guide_suffix}")
        ctx.complete_trace()

    def _answer_with_rag_iterative(self, ctx: _ReplyContext, tool_decision) -> None:
        bounded_simple_tools = tool_decision.max_tool_calls is not None
        ctx.event("rag_iterative_detected", "RAG-Iterativ erkannt", status="running",
                  summary=(
                      "Begrenzte agentische Code-Recherche wird gestartet"
                      if bounded_simple_tools
                      else "Iterative Datei-Analyse wird gestartet"
                  ),
                  details={
                      "trigger": tool_decision.trigger,
                      "profile_id": tool_decision.profile_id,
                      "tool_budget": tool_decision.max_tool_calls,
                  })
        started = time.time()
        cancel_keys = ctx.cancel_keys()
        cancel_event = register_chat_cancel(cancel_keys)
        try:
            answer, scan_trace = _worker_chat_rag_iterative(
                ctx.prompt,
                provider=ctx.provider,
                model=ctx.model,
                api_base=ctx.api_base,
                limits=ctx.ask_limits(),
                rec=ctx.rec,
                conversation_history=ctx.conversation_history,
                cancel_event=cancel_event,
                system_prompt=ctx.session_prompt,
                max_tool_calls_override=tool_decision.max_tool_calls,
                max_search_calls_override=tool_decision.max_search_calls,
                final_task_kind=tool_decision.final_task_kind,
            )
        finally:
            unregister_chat_cancel(cancel_keys, cancel_event)
        tool_loop = scan_trace.get("tool_loop") or {}
        cancelled = bool(scan_trace.get("cancelled") or tool_loop.get("cancelled"))
        scan_summary = rag_iterative_summary(scan_trace)
        if ctx.rec:
            synthesis_degraded = tool_loop.get("final_synthesis_status") == "completed_degraded"
            if synthesis_degraded:
                scan_summary += " — KAT-Synthese degradiert, LFM-Rechercheantwort verwendet"
            ctx.rec.event("rag_iterative_completed", "RAG-Iterativ abgeschlossen",
                          status=rag_iterative_trace_status(
                              cancelled=cancelled, synthesis_degraded=synthesis_degraded, answered=bool(answer)
                          ),
                          summary=scan_summary, duration_ms=(time.time() - started) * 1000,
                          details=scan_trace)
        if not answer:
            answer = "Anfrage abgebrochen." if cancelled else "RAG-Iterativ ergab keine Antwort."
        answer = self._fit_scan_answer(ctx, answer)
        self._publish(ctx, f"{answer}\n\n[{scan_summary}]{ctx.guide_suffix}")
        ctx.complete_trace()

    def _answer_with_full_scan(self, ctx: _ReplyContext) -> None:
        ctx.event("full_scan_detected", "Full-Scan erkannt", status="running",
                  summary="Architektur-Analyse wird gestartet")
        started = time.time()
        answer, scan_trace = _worker_chat_full_scan(
            ctx.prompt,
            provider=ctx.provider,
            model=ctx.model,
            limits=ctx.ask_limits(),
            cancel_key="room",
            conversation_history=ctx.conversation_history,
        )
        files_found = scan_trace.get("files_found", 0)
        batches_done = scan_trace.get("batches_completed", 0)
        scan_summary = f"full_scan: {batches_done} Batches, {files_found} Dateien"
        ctx.event(
            "full_scan_batch_completed", "Full-Scan abgeschlossen",
            status="completed" if answer else "failed",
            summary=scan_summary,
            duration_ms=(time.time() - started) * 1000,
            details={
                "files_found": files_found,
                "batches_completed": batches_done,
                "mode": scan_trace.get("mode"),
                "error": scan_trace.get("error"),
            },
        )
        answer = self._fit_scan_answer(ctx, answer or "Full-Scan ergab keine Antwort.")
        ctx.event("answer_postprocessed", "Antwort aufbereitet", status="completed", summary=f"{len(answer)} Zeichen")
        self._publish(ctx, f"{answer}\n\n[{scan_summary}]{ctx.guide_suffix}")
        ctx.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
        ctx.complete_trace()

    def _answer_with_grounded_delegation(self, ctx: _ReplyContext) -> None:
        ctx.event("retrieval_profile_selected", "Retrieval-Profil wird aufgelöst", status="running",
                  input_preview=ctx.prompt)
        grounded_prompt, has_context, context_summary = self._retrieve_grounding(ctx)
        asks_for_concrete_local_facts = any(token in ctx.prompt.lower() for token in _CONCRETE_FACT_TOKENS)
        # Skip the "no-context" short-circuit for ananta-settings (it intentionally has no RAG)
        if asks_for_concrete_local_facts and not has_context and not ctx.is_settings_profile:
            ctx.event("answer_postprocessed", "Anfrage ohne Kontext abgebrochen", status="skipped",
                      summary="Kein Kontext verfügbar für konkrete Fragen")
            self._publish(ctx, f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]")
            ctx.event("chat_message_written", "Hinweis in Raum geschrieben", status="completed")
            ctx.complete_trace()
            return

        answer = self._delegate_llm_answer(ctx, grounded_prompt)
        text = self._postprocess_answer(ctx, answer)
        text = f"{text}\n\n[{context_summary}]"
        ctx.event("answer_postprocessed", "Antwort aufbereitet", status="completed",
                  summary=f"{len(text)} Zeichen, Kontext angehängt")
        self._publish(ctx, f"{text}{ctx.guide_suffix}")
        ctx.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
        ctx.complete_trace()

    # ── grounded delegation steps ────────────────────────────────────────────

    @staticmethod
    def _retrieve_grounding(ctx: _ReplyContext) -> tuple[str, bool, str]:
        retrieval_start = time.time()
        ctx.event("codecompass_retrieval_started", "CodeCompass Retrieval gestartet", status="running",
                  input_preview=ctx.prompt)
        grounded_prompt, has_context, context_summary, domain_info, chunk_meta = _build_grounded_snake_prompt(
            ctx.prompt
        )
        ctx.event(
            "codecompass_retrieval_completed", "CodeCompass Retrieval abgeschlossen",
            status="completed" if has_context else "skipped",
            summary=context_summary,
            duration_ms=(time.time() - retrieval_start) * 1000,
            details={
                "has_context": has_context,
                "chunk_count": len(chunk_meta),
                "grounded_chars": len(grounded_prompt),
                "chunks": chunk_meta,
                "source_ranking": domain_info.get("source_ranking"),
            },
            output_preview=chunk_meta if chunk_meta else None,
        )
        ctx.event("prompt_built", "Prompt an LLM aufgebaut", status="completed",
                  summary=f"{len(grounded_prompt)} Zeichen Gesamtprompt, {len(chunk_meta)} Dateien eingebettet",
                  details={"context_summary": context_summary, "prompt_chars": len(grounded_prompt)},
                  output_preview=grounded_prompt)
        return grounded_prompt, has_context, context_summary

    def _delegate_llm_answer(self, ctx: _ReplyContext, grounded_prompt: str):
        # Use the active session's system prompt when set, otherwise fall back to the snake default
        system_prompt = ctx.session_prompt or _SNAKE_CHAT_PROMPT
        profile_routing = snake_profile_routing_enabled()
        llm_start = time.time()
        ctx.event("llm_call_started", "LLM-Aufruf gestartet", status="running",
                  summary=llm_call_started_summary(ctx.provider, ctx.model, len(grounded_prompt), profile_routing),
                  details={
                      "provider": ctx.provider,
                      "model": ctx.model,
                      "requested_provider_advisory": ctx.provider if profile_routing else None,
                      "requested_model_advisory": ctx.model if profile_routing else None,
                      "prompt_chars": len(grounded_prompt),
                      "system_prompt_chars": len(system_prompt),
                      "conversation_history_messages": len(ctx.conversation_history),
                  },
                  input_preview=grounded_prompt)
        budgeted_prompt = _with_answer_budget_instruction(
            grounded_prompt,
            ctx.answer_chars_limit,
            policy=_answer_overflow_policy(),
        )
        delegated_context = "\n".join(
            f"[{str(item.get('role') or 'context')}]: {str(item.get('content') or '')}"
            for item in ctx.conversation_history
            if isinstance(item, dict) and str(item.get("content") or "").strip()
        )
        delegated_prompt = (
            f"[system]: {system_prompt}\n"
            + (f"{delegated_context}\n" if delegated_context else "")
            + f"[user]: {budgeted_prompt}"
        )
        answer, delegated_trace = self._worker_propose(
            delegated_prompt,
            None,
            provider=ctx.provider,
            limits=ctx.ask_limits(),
            worker_picker=self._worker_picker,
            routing_task_kind=resolve_snake_routing_task_kind(ctx.prompt),
        )
        if not answer and not snake_profile_routing_enabled():
            answer = self._generate_text(
                prompt=budgeted_prompt,
                provider=ctx.provider,
                model=ctx.model,
                base_url=ctx.api_base,
                history=[{"role": "system", "content": system_prompt}, *ctx.conversation_history],
                timeout=_http_timeout_seconds(),
            )
        llm_ms = (time.time() - llm_start) * 1000
        ctx.event("llm_call_completed", "LLM-Aufruf abgeschlossen", status="completed",
                  duration_ms=llm_ms,
                  summary=f"{len(str(answer or ''))} Zeichen Antwort in {round(llm_ms / 1000, 1)}s",
                  details={"delegated_routing": delegated_trace},
                  output_preview=str(answer or ""))
        return answer

    @staticmethod
    def _postprocess_answer(ctx: _ReplyContext, answer) -> str:
        text = str(answer or "").strip()
        asked_for_link = any(token in ctx.prompt.lower() for token in ("link", "url", "quelle", "source"))
        if text and not asked_for_link:
            text = text.replace("http://", "").replace("https://", "")
        text = _fit_answer_to_chars(
            text,
            limit=ctx.answer_chars_limit,
            provider=ctx.provider,
            model=ctx.model,
            timeout=_http_timeout_seconds(),
            overflow_policy=_answer_overflow_policy(),
            never_truncate=_chat_never_truncate_answers(),
        )
        return text or "AI-Snake konnte gerade keine Antwort erzeugen."

    # ── shared output ────────────────────────────────────────────────────────

    @staticmethod
    def _fit_scan_answer(ctx: _ReplyContext, answer: str) -> str:
        return _fit_answer_to_chars(
            answer,
            limit=ctx.answer_chars_limit,
            provider=ctx.provider,
            model=ctx.model,
            timeout=int(ctx.chat_config.get("chat_ask_timeout_s") or 180),
            overflow_policy=_answer_overflow_policy(),
            never_truncate=_chat_never_truncate_answers(),
        )

    def _publish(self, ctx: _ReplyContext, text: str) -> None:
        self._append_room_message(text=text, session_id=ctx.session_id, owner_principal=ctx.owner_principal)
