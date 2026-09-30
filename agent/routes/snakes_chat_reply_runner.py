"""Background AI-Snake chat reply pipeline.

``SnakeChatReplyRunner`` produces one room reply for a chat message: it
resolves the session-bound provider, enriches the prompt with UI/settings
context, picks the answer strategy (Ananta-config tool loop, bounded agentic
RAG, full scan or grounded single-shot delegation) and writes the answer plus
trace events. :mod:`.snakes_chat_reply_spawner` owns the thread and the
UI-state store and injects the collaborators here as narrow callables.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from copy import deepcopy
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
            _intent = prompt[7:].strip()
            if _intent:
                _eff_sess = str((session_snapshot or {}).get("id") or "")
                _ui_ctx_now = self._ui_state.get(snake_id or "") or {}
                self._append_room_message(
                    text=f"Guide wird gestartet: {_intent[:100]}…",
                    session_id=_eff_sess,
                    owner_principal=owner_principal,
                )
                from agent.services.visual_guide.service import _visual_guide_service as _vgs
                _vgs.handle_manual_guide(
                    snake_id=snake_id or "",
                    intent=_intent,
                    snapshot=str(_ui_ctx_now.get("ui_snapshot") or ""),
                    route=str(_ui_ctx_now.get("route") or ""),
                    owner_principal=owner_principal,
                )
            return

        rec = None
        store = None
        trace_id = None
        try:
            if _trace_feature_enabled():
                from agent.routes.ai_snake_config import _current_config as _trc_cfg
                from agent.routes.ai_snake_trace_store import TraceRecorder, get_trace_store
                _trc_settings = _trc_cfg()
                _max_preview = int(_trc_settings.get("ai_snake_trace_max_preview_chars") or 200000)
                store = get_trace_store()
                trace_id = store.new_trace(
                    snake_id=snake_id,
                    session_id=client_session_id or None,
                )
                rec = TraceRecorder(store, trace_id, max_preview_chars=_max_preview)
                _prompt_preview = prompt[:120] + ("…" if len(prompt) > 120 else "")
                rec.event(
                    "request_received", "Anfrage empfangen",
                    status="completed",
                    summary=f"Prompt: {_prompt_preview}",
                )

            conversation_history = context_history if context_history is not None else _build_room_conversation_history(
                snake_id=snake_id,
                current_text=prompt,
                session_id=client_session_id,
                owner_principal=owner_principal,
            )
            # The request thread resolves and authorizes the exact session.
            # Background work consumes only that immutable snapshot and never
            # re-enters global chat state or an active-session fallback.
            _authorized_session = deepcopy(session_snapshot) if session_snapshot else {}
            _active_session_id = (
                str(_authorized_session.get("id") or "")
                if _authorized_session
                else ""
            )
            _active_session_prompt = str(_authorized_session.get("system_prompt") or "").strip() or None
            _active_session_group = str(_authorized_session.get("group") or "").strip()
            _active_session_settings = dict(_authorized_session.get("settings") or {})
            _active_profile_id = str(_authorized_session.get("profile_id") or "")
            self._logger.info(
                "chat session resolved: active_session_id=%r client_session_id=%r",
                _active_session_id, client_session_id,
            )

            from agent.routes.ai_snake_config import _current_config as _provider_config
            _effective_provider_config = _provider_config()
            if _active_session_settings:
                _effective_provider_config = {**_effective_provider_config, **_active_session_settings}
            provider, model, api_base = self._resolve_chat_provider(_effective_provider_config)
            if rec:
                rec.event(
                    "config_loaded",
                    "Angeforderte Session-Konfiguration geladen",
                    status="completed",
                    details={
                        "requested_provider": provider,
                        "requested_model": model,
                        "session_requested_provider": provider,
                        "session_requested_model": model,
                        "backend": _effective_provider_config.get("chat_backend"),
                        "effective_runtime": (
                            "hub_worker_profile_routing"
                            if snake_profile_routing_enabled()
                            else "legacy_direct_provider"
                        ),
                        "routing_authority": (
                            "local_model_profile"
                            if snake_profile_routing_enabled()
                            else "session_provider_config"
                        ),
                        "session_metadata_advisory": snake_profile_routing_enabled(),
                        "effective_provider": (
                            "hub_worker_profile" if snake_profile_routing_enabled() else provider
                        ),
                        "effective_model": (
                            "resolved_per_task" if snake_profile_routing_enabled() else model
                        ),
                        "expected_research_profile": (
                            "local_lfm25_agentic_fast"
                            if snake_profile_routing_enabled()
                            else None
                        ),
                        "expected_synthesis_profile": (
                            "local_kat_coder_v25_heavy"
                            if snake_profile_routing_enabled()
                            else None
                        ),
                        "session_id": _active_session_id,
                        "conversation_history_messages": len(conversation_history),
                    },
                )

            # Ananta-Settings session: enrich prompt with current settings context
            _original_prompt = prompt
            _is_settings_profile = _active_profile_id == "ananta-settings" or _active_session_id == "ananta-settings"
            if _is_settings_profile:
                # Resolve effective UI context: per-message > continuous push > empty
                _effective_ui_ctx = (ui_context or {}) or (self._ui_state.get(snake_id or "") if snake_id else {}) or {}
                _settings_ctx = _read_ananta_settings_summary()
                if _effective_ui_ctx:
                    _ui_route = _effective_ui_ctx.get("route", "?")
                    _ui_waypoints = ", ".join(_effective_ui_ctx.get("visible_waypoints") or []) or "(keine)"
                    _ui_surface = _effective_ui_ctx.get("active_surface", "")
                    _ui_snapshot = str(_effective_ui_ctx.get("ui_snapshot") or "").strip()
                    _ui_ctx_block = (
                        "[Aktueller UI-Kontext]\n"
                        + (f"UI-Ansicht: {_ui_snapshot}\n" if _ui_snapshot else f"Route: {_ui_route}\n")
                        + (f"Surface: {_ui_surface}\n" if _ui_surface and not _ui_snapshot else "")
                        + (f"Waypoints: {_ui_waypoints}\n" if not _ui_snapshot else "")
                        + "\n"
                    )
                    prompt = f"{_ui_ctx_block}[Aktuelle Ananta-Konfiguration]\n{_settings_ctx}\n\n[Nutzerfrage]\n{prompt}"
                else:
                    prompt = f"[Aktuelle Ananta-Konfiguration]\n{_settings_ctx}\n\n[Nutzerfrage]\n{prompt}"

            elif _should_include_light_ui_context(
                active_session_id=_active_session_id,
                active_session_group=_active_session_group,
                active_session_settings=_active_session_settings,
                prompt=_original_prompt,
            ):
                # Lightweight UI context for sessions that explicitly benefit from UI state.
                _light_ui = (ui_context or {}) or (self._ui_state.get(snake_id or "") if snake_id else {}) or {}
                if _light_ui:
                    _light_hint = str(_light_ui.get("ui_snapshot") or _light_ui.get("route") or "").strip()
                    if _light_hint:
                        prompt = f"[UI-Kontext: {_light_hint[:100]}]\n\n{prompt}"

            # Compute guide suffix for ananta-settings session (used below in all emit paths)
            import json as _json
            _guide_suffix = ""
            if _is_settings_profile:
                _guide = _build_ui_guide(_original_prompt)
                if _guide:
                    _guide_suffix = f"\n\n__GUIDE__:{_json.dumps(_guide, ensure_ascii=False)}"

            _answer_chars_limit = _chat_answer_chars_limit()
            try:
                from agent.routes.ai_snake_config import _current_config
                from agent.services.retrieval_profile_service import _is_full_scan_intent
                from agent.services.snake_agentic_tool_policy import (
                    resolve_snake_agentic_tool_decision,
                )
                _cfg = _current_config()
                # Apply session-level setting overrides so they take precedence over global config.
                # For ananta-settings: force disable RAG/code-analysis regardless of persisted values,
                # since legacy persisted sessions may have rag_iterative from before the session existed.
                if _is_settings_profile:
                    _cfg = {
                        **_cfg,
                        "chat_architecture_analysis_mode": False,
                        "chat_retrieval_profile": "none",
                        "chat_use_codecompass": False,
                        "chat_code_questions_repo_first": False,
                        "chat_include_local_project": False,
                        **({"chat_answer_chars": 3000} if not _cfg.get("chat_answer_chars") else {}),
                    }
                elif _active_session_settings:
                    _cfg = {**_cfg, **_active_session_settings}
                _answer_chars_limit = _chat_answer_chars_limit()

                # ananta-settings: dedicated config tool loop (search_ui_docs, read_ananta_config, get_hub_*)
                if _is_settings_profile:
                    from agent.routes.snakes_ananta_config_tool_loop import run_ananta_config_tool_loop
                    if rec:
                        rec.event("ananta_config_tool_loop_start", "Ananta-Konfig Tool-Loop gestartet",
                                  status="running", summary="Konfigurations-Guide mit Tool-Calling aktiv")
                    _t0_cfg = time.time()
                    _cancel_keys_cfg = ["room"] + ([snake_id] if snake_id else [])
                    _cancel_event_cfg = register_chat_cancel(_cancel_keys_cfg)
                    try:
                        _cfg_messages = [
                            {"role": "system", "content": _active_session_prompt or _SNAKE_CHAT_PROMPT},
                            *conversation_history,
                            {"role": "user", "content": prompt},
                        ]
                        _cfg_answer, _cfg_trace = run_ananta_config_tool_loop(
                            messages=_cfg_messages,
                            provider=provider,
                            model=model,
                            api_base=api_base,
                            max_tool_calls=8,
                            timeout=120,
                            cancel_event=_cancel_event_cfg,
                        )
                    finally:
                        unregister_chat_cancel(_cancel_keys_cfg, _cancel_event_cfg)
                    _tc_made = _cfg_trace.get("tool_calls_made", 0)
                    _tools_str = ", ".join(_cfg_trace.get("tools_used") or []) or "–"
                    _cfg_summary = f"ananta-config: {_tc_made} Tool-Calls [{_tools_str}]"
                    if rec:
                        rec.event("ananta_config_tool_loop_done", "Ananta-Konfig Tool-Loop abgeschlossen",
                                  status="completed" if _cfg_answer else "failed",
                                  summary=_cfg_summary,
                                  duration_ms=(time.time() - _t0_cfg) * 1000,
                                  details=_cfg_trace)
                    if not _cfg_answer:
                        _cfg_answer = "Keine Antwort vom Konfigurations-Guide."
                    self._append_room_message(
                        text=f"{_cfg_answer}\n\n[{_cfg_summary}]{_guide_suffix}",
                        session_id=_active_session_id,
                        owner_principal=owner_principal,
                    )
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return

                _tool_decision = resolve_snake_agentic_tool_decision(prompt, _cfg)
                if _tool_decision.enabled:
                    _bounded_simple_tools = _tool_decision.max_tool_calls is not None
                    if rec:
                        rec.event("rag_iterative_detected", "RAG-Iterativ erkannt", status="running",
                                  summary=(
                                      "Begrenzte agentische Code-Recherche wird gestartet"
                                      if _bounded_simple_tools
                                      else "Iterative Datei-Analyse wird gestartet"
                                  ),
                                  details={
                                      "trigger": _tool_decision.trigger,
                                      "profile_id": _tool_decision.profile_id,
                                      "tool_budget": _tool_decision.max_tool_calls,
                                  })
                    t0 = time.time()
                    _cancel_keys = ["room"] + ([snake_id] if snake_id else [])
                    _cancel_event = register_chat_cancel(_cancel_keys)
                    try:
                        answer, scan_trace = _worker_chat_rag_iterative(
                            prompt,
                            provider=provider,
                            model=model,
                            api_base=api_base,
                            limits=SnakeAskLimits(
                                answer_chars=_answer_chars_limit,
                                answer_overflow_policy=_answer_overflow_policy(),
                                never_truncate_answers=_chat_never_truncate_answers(),
                            ),
                            rec=rec,
                            conversation_history=conversation_history,
                            cancel_event=_cancel_event,
                            system_prompt=_active_session_prompt,
                            max_tool_calls_override=_tool_decision.max_tool_calls,
                            max_search_calls_override=_tool_decision.max_search_calls,
                            final_task_kind=_tool_decision.final_task_kind,
                        )
                    finally:
                        unregister_chat_cancel(_cancel_keys, _cancel_event)
                    _tl = scan_trace.get("tool_loop") or {}
                    if scan_trace.get("cancelled") or _tl.get("cancelled"):
                        scan_summary = "rag_iterative: abgebrochen"
                    elif _tl or scan_trace.get("available_files"):
                        _avail = scan_trace.get("available_files") or []
                        _tc_made = _tl.get("tool_calls_made", 0)
                        file_names = ", ".join(str(p).split("/")[-1] for p in _avail[:6])
                        if len(_avail) > 6:
                            file_names += f" +{len(_avail) - 6}"
                        scan_summary = f"rag_iterative: {_tc_made} Tool-Calls, {len(_avail)} Dateien verfügbar" + (f" ({file_names})" if file_names else "")
                    else:
                        batches_done = scan_trace.get("batches_completed", 0)
                        files_found = scan_trace.get("files_resolved", 0)
                        file_list = scan_trace.get("file_list") or []
                        file_names = ", ".join(str(p).split("/")[-1] for p in file_list[:6])
                        if len(file_list) > 6:
                            file_names += f" +{len(file_list) - 6}"
                        scan_summary = f"rag_iterative: {batches_done} Batches, {files_found} Dateien" + (f" ({file_names})" if file_names else "")
                    if rec:
                        _synthesis_degraded = (
                            _tl.get("final_synthesis_status") == "completed_degraded"
                        )
                        if _synthesis_degraded:
                            scan_summary += " — KAT-Synthese degradiert, LFM-Rechercheantwort verwendet"
                        rec.event("rag_iterative_completed", "RAG-Iterativ abgeschlossen",
                                  status="cancelled" if scan_trace.get("cancelled") or _tl.get("cancelled") else ("warning" if _synthesis_degraded else ("completed" if answer else "failed")),
                                  summary=scan_summary, duration_ms=(time.time() - t0) * 1000,
                                  details=scan_trace)
                    if not answer:
                        answer = "Anfrage abgebrochen." if scan_trace.get("cancelled") or _tl.get("cancelled") else "RAG-Iterativ ergab keine Antwort."
                    answer = _fit_answer_to_chars(
                        answer,
                        limit=_answer_chars_limit,
                        provider=provider,
                        model=model,
                        timeout=int(_cfg.get("chat_ask_timeout_s") or 180),
                        overflow_policy=_answer_overflow_policy(),
                        never_truncate=_chat_never_truncate_answers(),
                    )
                    self._append_room_message(
                        text=f"{answer}\n\n[{scan_summary}]{_guide_suffix}",
                        session_id=_active_session_id,
                        owner_principal=owner_principal,
                    )
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return
                elif _is_full_scan_intent(prompt, "", _cfg):
                    if rec:
                        rec.event("full_scan_detected", "Full-Scan erkannt", status="running",
                                  summary="Architektur-Analyse wird gestartet")
                    t0 = time.time()
                    answer, scan_trace = _worker_chat_full_scan(
                        prompt,
                        provider=provider,
                        model=model,
                        limits=SnakeAskLimits(
                            answer_chars=_answer_chars_limit,
                            answer_overflow_policy=_answer_overflow_policy(),
                            never_truncate_answers=_chat_never_truncate_answers(),
                        ),
                        cancel_key="room",
                        conversation_history=conversation_history,
                    )
                    files_found = scan_trace.get("files_found", 0)
                    batches_done = scan_trace.get("batches_completed", 0)
                    scan_summary = f"full_scan: {batches_done} Batches, {files_found} Dateien"
                    if rec:
                        rec.event(
                            "full_scan_batch_completed", "Full-Scan abgeschlossen",
                            status="completed" if answer else "failed",
                            summary=scan_summary,
                            duration_ms=(time.time() - t0) * 1000,
                            details={
                                "files_found": files_found,
                                "batches_completed": batches_done,
                                "mode": scan_trace.get("mode"),
                                "error": scan_trace.get("error"),
                            },
                        )
                    if not answer:
                        answer = "Full-Scan ergab keine Antwort."
                    answer = _fit_answer_to_chars(
                        answer,
                        limit=_answer_chars_limit,
                        provider=provider,
                        model=model,
                        timeout=int(_cfg.get("chat_ask_timeout_s") or 180),
                        overflow_policy=_answer_overflow_policy(),
                        never_truncate=_chat_never_truncate_answers(),
                    )
                    if rec:
                        rec.event("answer_postprocessed", "Antwort aufbereitet", status="completed",
                                  summary=f"{len(answer)} Zeichen")
                    self._append_room_message(
                        text=f"{answer}\n\n[{scan_summary}]{_guide_suffix}",
                        session_id=_active_session_id,
                        owner_principal=owner_principal,
                    )
                    if rec:
                        rec.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return
            except Exception as exc:
                self._logger.debug("full_scan check failed, falling back: %s", exc)

            if rec:
                rec.event("retrieval_profile_selected", "Retrieval-Profil wird aufgelöst", status="running",
                          input_preview=prompt)

            retrieval_start = time.time()
            if rec:
                rec.event("codecompass_retrieval_started", "CodeCompass Retrieval gestartet", status="running",
                          input_preview=prompt)

            grounded_prompt, has_context, context_summary, _domain_info, chunk_meta = _build_grounded_snake_prompt(prompt)

            retrieval_ms = (time.time() - retrieval_start) * 1000
            if rec:
                rec.event(
                    "codecompass_retrieval_completed", "CodeCompass Retrieval abgeschlossen",
                    status="completed" if has_context else "skipped",
                    summary=context_summary,
                    duration_ms=retrieval_ms,
                    details={
                        "has_context": has_context,
                        "chunk_count": len(chunk_meta),
                        "grounded_chars": len(grounded_prompt),
                        "chunks": chunk_meta,
                        "source_ranking": _domain_info.get("source_ranking"),
                    },
                    output_preview=chunk_meta if chunk_meta else None,
                )
                rec.event("prompt_built", "Prompt an LLM aufgebaut", status="completed",
                          summary=f"{len(grounded_prompt)} Zeichen Gesamtprompt, {len(chunk_meta)} Dateien eingebettet",
                          details={"context_summary": context_summary, "prompt_chars": len(grounded_prompt)},
                          output_preview=grounded_prompt)

            q = prompt.lower()
            asks_for_concrete_local_facts = any(
                token in q for token in (
                    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verfügbar"
                )
            )
                        # Skip the "no-context" short-circuit for ananta-settings (it intentionally has no RAG)
            if asks_for_concrete_local_facts and not has_context and not _is_settings_profile:
                if rec:
                    rec.event("answer_postprocessed", "Anfrage ohne Kontext abgebrochen", status="skipped",
                              summary="Kein Kontext verfügbar für konkrete Fragen")
                self._append_room_message(
                    text=f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]",
                    session_id=_active_session_id,
                    owner_principal=owner_principal,
                )
                if rec:
                    rec.event("chat_message_written", "Hinweis in Raum geschrieben", status="completed")
                if store and trace_id:
                    store.complete_trace(trace_id)
                return

            # Use the active session's system prompt when set, otherwise fall back to the snake default
            _effective_system_prompt = _active_session_prompt or _SNAKE_CHAT_PROMPT

            llm_start = time.time()
            if rec:
                rec.event("llm_call_started", "LLM-Aufruf gestartet", status="running",
                          summary=(
                              "lokales Hub-Worker-Profil — "
                              if snake_profile_routing_enabled()
                              else f"{provider} / {model or 'default'} — "
                          ) + f"{len(grounded_prompt)} Zeichen Eingabe",
                          details={
                              "provider": provider,
                              "model": model,
                              "requested_provider_advisory": provider if snake_profile_routing_enabled() else None,
                              "requested_model_advisory": model if snake_profile_routing_enabled() else None,
                              "prompt_chars": len(grounded_prompt),
                              "system_prompt_chars": len(_effective_system_prompt),
                              "conversation_history_messages": len(conversation_history),
                          },
                          input_preview=grounded_prompt)

            budgeted_prompt = _with_answer_budget_instruction(
                grounded_prompt,
                _answer_chars_limit,
                policy=_answer_overflow_policy(),
            )
            delegated_context = "\n".join(
                f"[{str(item.get('role') or 'context')}]: {str(item.get('content') or '')}"
                for item in conversation_history
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            )
            delegated_prompt = (
                f"[system]: {_effective_system_prompt}\n"
                + (f"{delegated_context}\n" if delegated_context else "")
                + f"[user]: {budgeted_prompt}"
            )
            answer, delegated_trace = self._worker_propose(
                delegated_prompt,
                None,
                provider=provider,
                limits=SnakeAskLimits(
                    answer_chars=_answer_chars_limit,
                    answer_overflow_policy=_answer_overflow_policy(),
                    never_truncate_answers=_chat_never_truncate_answers(),
                ),
                worker_picker=self._worker_picker,
                routing_task_kind=resolve_snake_routing_task_kind(prompt),
            )
            if not answer and not snake_profile_routing_enabled():
                answer = self._generate_text(
                    prompt=budgeted_prompt,
                    provider=provider,
                    model=model,
                    base_url=api_base,
                    history=[{"role": "system", "content": _effective_system_prompt}, *conversation_history],
                    timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
                )

            llm_ms = (time.time() - llm_start) * 1000
            if rec:
                rec.event("llm_call_completed", "LLM-Aufruf abgeschlossen", status="completed",
                          duration_ms=llm_ms,
                          summary=f"{len(str(answer or ''))} Zeichen Antwort in {round(llm_ms / 1000, 1)}s",
                          details={"delegated_routing": delegated_trace},
                          output_preview=str(answer or ""))

            text = str(answer or "").strip()
            asked_for_link = any(token in prompt.lower() for token in ("link", "url", "quelle", "source"))
            if text and not asked_for_link:
                text = text.replace("http://", "").replace("https://", "")
            text = _fit_answer_to_chars(
                text,
                limit=_answer_chars_limit,
                provider=provider,
                model=model,
                timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
                overflow_policy=_answer_overflow_policy(),
                never_truncate=_chat_never_truncate_answers(),
            )
            if not text:
                text = "AI-Snake konnte gerade keine Antwort erzeugen."
            text = f"{text}\n\n[{context_summary}]"

            if rec:
                rec.event("answer_postprocessed", "Antwort aufbereitet", status="completed",
                          summary=f"{len(text)} Zeichen, Kontext angehängt")

            self._append_room_message(
                text=f"{text}{_guide_suffix}",
                session_id=_active_session_id,
                owner_principal=owner_principal,
            )

            if rec:
                rec.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
            if store and trace_id:
                store.complete_trace(trace_id)

        except Exception as exc:
            self._logger.warning("ai-snake-chat-reply failed: %s", exc)
            if rec and store and trace_id:
                try:
                    rec.event("failed", "Fehler bei der Antwortgenerierung", status="failed",
                              error=str(exc)[:300])
                    store.complete_trace(trace_id, status="failed")
                except Exception:
                    pass
            self._append_room_message(
                text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.",
                session_id=_active_session_id,
                owner_principal=owner_principal,
            )
