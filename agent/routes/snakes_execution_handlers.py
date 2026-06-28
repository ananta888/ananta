"""Snake execution endpoint implementations — chat API, ask, worker-context."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, has_app_context, jsonify, request, Response

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.rag_service import get_rag_service
from agent.services.snake_chat_cancellation import (
    cancel_chat,
    register_chat_cancel,
    unregister_chat_cancel,
)

from .snakes_state import (
    _MAX_CHAT_MSGS,
    _MAX_ROOM_MSGS,
    _SCAN_CANCELS,
    _VALID_CHANNEL_TYPES,
    _VALID_VISIBILITY,
    _chat_messages,
    _is_local_request,
    _optional_user_auth,
    _request_device_id,
    _room_messages,
    _snake_bound_to_auth,
    _snakes,
    snakes_bp,
)
from .snake_event_broadcaster import (
    broadcast_snake_event,
    drop_snake_queue,
    get_snake_event,
)
from .snakes_chat_helpers import (
    SnakeAskLimits,
    _ANANTA_UI_GUIDE_MAP,
    _answer_budget_instruction,
    _answer_overflow_policy,
    _append_room_ai_message,
    _bounded_optional_int,
    _build_grounded_snake_prompt,
    _build_room_conversation_history,
    _build_ui_guide,
    _chat_answer_chars_limit,
    _chat_never_truncate_answers,
    _ensure_ui_guide,
    _fit_answer_to_chars,
    _optional_bool,
    _read_ananta_settings_summary,
    _should_include_light_ui_context,
    _trace_feature_enabled,
    _with_answer_budget_instruction,
)
from .snakes_retrieval_helpers import (
    _SNAKE_RETRIEVAL_CONFIG_KEYS,
    _build_local_repo_fallback_context,
    _domain_scope_response,
    _resolve_domain_scope_for_chat,
    _resolve_snake_retrieval_profile_trace,
    _snake_retrieval_config_overrides,
    _snake_retrieval_dry_run,
)
from .snakes_visual_guide import (
    _VISUAL_GUIDE_EXECUTOR,
    _VISUAL_SESSION_ID,
    _VISUAL_THROTTLE_S,
    _append_visual_user_tick,
    _get_visual_state_ref,
    _spawn_region_explain_reply,
    _spawn_visual_reply,
    _visual_session_log_deltas_only,
    _visual_session_settings,
)
from .snakes_worker_routing import (
    _auth_token,
    _pick_worker_for_ask,
    _resolve_lmstudio_model_for_worker,
    _verify_token,
    _worker_propose,
)
from .snakes_full_scan import _SCAN_CANCELS as _FULL_SCAN_CANCELS
from .snakes_full_scan import worker_chat_full_scan as _worker_chat_full_scan
from .snakes_rag_iterative import worker_chat_rag_iterative as _worker_chat_rag_iterative

# In-memory UI state pushed by the browser via PUT /snakes/<id>/ui-state.
# Keyed by snake_id; used to enrich LLM prompts with current navigation context.
_snake_ui_state: dict[str, dict] = {}

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


def _background_threads_disabled() -> bool:
    return bool(
        (has_app_context() and bool(getattr(current_app, "testing", False)))
        or str(getattr(settings, "role", "")).strip().lower() == "test"
        or os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


def _resolve_ai_snake_chat_provider() -> tuple[str, str | None, str | None]:
    provider = "lmstudio"
    model: str | None = None
    api_base: str | None = None
    try:
        from agent.routes.ai_snake_config import _current_config

        cfg = _current_config()
        configured_model = str(cfg.get("chat_backend_model") or "").strip() or None
        configured_api_base = str(cfg.get("chat_backend_api_base") or "").strip() or None
        if configured_model:
            model = configured_model

        _openai_models = ("gpt-4", "gpt-3.5", "gpt-4o", "o1", "o3")
        is_openai_model = any(model.startswith(m) for m in _openai_models) if model else False
        is_openai_url = configured_api_base and "openai.com" in configured_api_base.lower()

        if is_openai_url or is_openai_model:
            provider = "openai"
            if configured_api_base:
                api_base = configured_api_base.rstrip("/") + "/chat/completions"
    except Exception:
        pass
    return provider, model, api_base


def _spawn_ai_chat_reply(*, user_text: str, snake_id: str | None = None, ui_context: dict | None = None, client_session_id: str = "") -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        nonlocal prompt

        # Handle /guide intent — bypass normal LLM, trigger visual guide
        if prompt.startswith("/guide "):
            _intent = prompt[7:].strip()
            if _intent:
                _eff_sess = client_session_id if client_session_id and client_session_id != "ananta-visual" else "ananta-settings"
                _ui_ctx_now = _snake_ui_state.get(snake_id or "") or {}
                _append_room_ai_message(
                    text=f"Guide wird gestartet: {_intent[:100]}…",
                    session_id=_eff_sess,
                )
                from agent.services.visual_guide.service import _visual_guide_service as _vgs
                _vgs.handle_manual_guide(
                    snake_id=snake_id or "",
                    intent=_intent,
                    snapshot=str(_ui_ctx_now.get("ui_snapshot") or ""),
                    route=str(_ui_ctx_now.get("route") or ""),
                )
            return

        rec = None
        store = None
        trace_id = None
        try:
            if _trace_feature_enabled():
                from agent.routes.ai_snake_trace_store import get_trace_store, TraceRecorder
                from agent.routes.ai_snake_config import _current_config as _trc_cfg
                _trc_settings = _trc_cfg()
                _max_preview = int(_trc_settings.get("ai_snake_trace_max_preview_chars") or 200000)
                store = get_trace_store()
                trace_id = store.new_trace(snake_id=snake_id)
                rec = TraceRecorder(store, trace_id, max_preview_chars=_max_preview)
                _prompt_preview = prompt[:120] + ("…" if len(prompt) > 120 else "")
                rec.event(
                    "request_received", "Anfrage empfangen",
                    status="completed",
                    summary=f"Prompt: {_prompt_preview}",
                )

            provider, model, api_base = _resolve_ai_snake_chat_provider()
            conversation_history = _build_room_conversation_history(
                snake_id=snake_id,
                current_text=prompt,
                session_id=client_session_id,
            )
            if rec:
                rec.event("config_loaded", "Provider-Konfiguration geladen", status="completed",
                          details={"provider": provider, "model": model, "conversation_history_messages": len(conversation_history)})

            # Resolve active session's system_prompt, ID, and settings overrides
            _active_session_prompt: str | None = None
            _active_session_id: str = ""
            _active_session_group: str = ""
            _active_session_settings: dict = {}
            try:
                from client_surfaces.operator_tui.config.user_config_manager import get_manager as _get_mgr2
                _stored2 = _get_mgr2().load()
                _active_sid2 = str(_stored2.get("chat_active_session_id") or "").strip()
                _active_session_id = _active_sid2
                if _active_sid2:
                    for _sess2 in (_stored2.get("chat_sessions") or []):
                        if str(_sess2.get("id") or "") == _active_sid2:
                            _active_session_prompt = str(_sess2.get("system_prompt") or "").strip() or None
                            _active_session_group = str(_sess2.get("group") or "").strip()
                            _active_session_settings = dict(_sess2.get("settings") or {})
                            break
            except Exception:
                pass
            # If the frontend sent an explicit session_id, use it directly (avoids user.json race conditions
            # when the snake panel session and AI Chats page session diverge).
            if client_session_id and client_session_id != _active_session_id:
                _active_session_id = client_session_id
                # Find settings for this session in user.json
                try:
                    for _sess2 in (_stored2.get("chat_sessions") or []):
                        if str(_sess2.get("id") or "") == client_session_id:
                            _active_session_group = str(_sess2.get("group") or "").strip()
                            _active_session_settings = dict(_sess2.get("settings") or {})
                            break
                except Exception:
                    pass
            logging.getLogger(__name__).info(
                "chat session resolved: active_session_id=%r client_session_id=%r",
                _active_session_id, client_session_id,
            )

            # For built-in sessions, always use the canonical system_prompt from DEFAULT_SESSIONS
            # so code changes to prompts take effect immediately without requiring user.json migration.
            try:
                from client_surfaces.operator_tui.chat_state import DEFAULT_SESSIONS as _DS2
                for _ds in _DS2:
                    if str(_ds.get("id") or "") == _active_session_id:
                        _active_session_group = str(_ds.get("group") or _active_session_group or "").strip()
                        _canonical_prompt = str(_ds.get("system_prompt") or "").strip()
                        if _canonical_prompt:
                            _active_session_prompt = _canonical_prompt
                        break
            except Exception:
                pass

            # Ananta-Settings session: enrich prompt with current settings context
            _original_prompt = prompt
            if _active_session_id == "ananta-settings":
                # Resolve effective UI context: per-message > continuous push > empty
                _effective_ui_ctx = (ui_context or {}) or (_snake_ui_state.get(snake_id or "") if snake_id else {}) or {}
                _settings_ctx = _read_ananta_settings_summary()
                if _effective_ui_ctx:
                    _ui_route = _effective_ui_ctx.get("route", "?")
                    _ui_waypoints = ", ".join(_effective_ui_ctx.get("visible_waypoints") or []) or "(keine)"
                    _ui_surface = _effective_ui_ctx.get("active_surface", "")
                    _ui_snapshot = str(_effective_ui_ctx.get("ui_snapshot") or "").strip()
                    _ui_ctx_block = (
                        f"[Aktueller UI-Kontext]\n"
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
            ):
                # Lightweight UI context for sessions that explicitly benefit from UI state.
                _light_ui = (ui_context or {}) or (_snake_ui_state.get(snake_id or "") if snake_id else {}) or {}
                if _light_ui:
                    _light_hint = str(_light_ui.get("ui_snapshot") or _light_ui.get("route") or "").strip()
                    if _light_hint:
                        prompt = f"[UI-Kontext: {_light_hint[:100]}]\n\n{prompt}"

            # Compute guide suffix for ananta-settings session (used below in all emit paths)
            import json as _json
            _guide_suffix = ""
            if _active_session_id == "ananta-settings":
                _guide = _build_ui_guide(_original_prompt)
                if _guide:
                    _guide_suffix = f"\n\n__GUIDE__:{_json.dumps(_guide, ensure_ascii=False)}"

            _answer_chars_limit = _chat_answer_chars_limit()
            try:
                from agent.routes.ai_snake_config import _current_config
                from agent.services.retrieval_profile_service import _is_full_scan_intent, _is_rag_iterative_intent
                _cfg = _current_config()
                # Apply session-level setting overrides so they take precedence over global config.
                # For ananta-settings: force disable RAG/code-analysis regardless of persisted values,
                # since legacy persisted sessions may have rag_iterative from before the session existed.
                if _active_session_id == "ananta-settings":
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
                if _active_session_id == "ananta-settings":
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
                    _append_room_ai_message(text=f"{_cfg_answer}\n\n[{_cfg_summary}]{_guide_suffix}", session_id=_active_session_id)
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return

                if _is_rag_iterative_intent(_cfg):
                    if rec:
                        rec.event("rag_iterative_detected", "RAG-Iterativ erkannt", status="running",
                                  summary="Iterative Datei-Analyse wird gestartet")
                    t0 = time.time()
                    _cancel_keys = ["room"] + ([snake_id] if snake_id else [])
                    _cancel_event = register_chat_cancel(_cancel_keys)
                    try:
                        answer, scan_trace = _worker_chat_rag_iterative(
                            prompt,
                            provider=provider,
                            model=model,
                            limits=SnakeAskLimits(
                                answer_chars=_answer_chars_limit,
                                answer_overflow_policy=_answer_overflow_policy(),
                                never_truncate_answers=_chat_never_truncate_answers(),
                            ),
                            rec=rec,
                            conversation_history=conversation_history,
                            cancel_event=_cancel_event,
                            system_prompt=_active_session_prompt,
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
                        rec.event("rag_iterative_completed", "RAG-Iterativ abgeschlossen",
                                  status="cancelled" if scan_trace.get("cancelled") or _tl.get("cancelled") else ("completed" if answer else "failed"),
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
                    _append_room_ai_message(text=f"{answer}\n\n[{scan_summary}]{_guide_suffix}", session_id=_active_session_id)
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
                    _append_room_ai_message(text=f"{answer}\n\n[{scan_summary}]{_guide_suffix}", session_id=_active_session_id)
                    if rec:
                        rec.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return
            except Exception as exc:
                logging.getLogger(__name__).debug("full_scan check failed, falling back: %s", exc)

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
            if asks_for_concrete_local_facts and not has_context and _active_session_id != "ananta-settings":
                if rec:
                    rec.event("answer_postprocessed", "Anfrage ohne Kontext abgebrochen", status="skipped",
                              summary="Kein Kontext verfügbar für konkrete Fragen")
                _append_room_ai_message(text=f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]", session_id=_active_session_id)
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
                          summary=f"{provider} / {model or 'default'} — {len(grounded_prompt)} Zeichen Eingabe",
                          details={
                              "provider": provider,
                              "model": model,
                              "prompt_chars": len(grounded_prompt),
                              "system_prompt_chars": len(_effective_system_prompt),
                              "conversation_history_messages": len(conversation_history),
                          },
                          input_preview=grounded_prompt)

            answer = generate_text(
                prompt=_with_answer_budget_instruction(
                    grounded_prompt,
                    _answer_chars_limit,
                    policy=_answer_overflow_policy(),
                ),
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

            _append_room_ai_message(text=f"{text}{_guide_suffix}", session_id=_active_session_id)

            if rec:
                rec.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
            if store and trace_id:
                store.complete_trace(trace_id)

        except Exception as exc:
            logging.getLogger(__name__).warning("ai-snake-chat-reply failed: %s", exc)
            if rec and store and trace_id:
                try:
                    rec.event("failed", "Fehler bei der Antwortgenerierung", status="failed",
                              error=str(exc)[:300])
                    store.complete_trace(trace_id, status="failed")
                except Exception:
                    pass
            _append_room_ai_message(text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.", session_id=_active_session_id)

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()


# ── Route endpoints ────────────────────────────────────────────────────────────


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["POST"])
def chat_send(snake_id: str):
    """POST /snakes/<id>/chat/messages -- ChatMessage-v1 senden."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    auth = _optional_user_auth()
    if not auth and not _is_local_request():
        return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401
    snake = _snakes.get(snake_id) or {}
    if auth and not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_identity_mismatch"}), 403

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    channel_type = str(body.get("channel_type") or "room")
    visibility = str(body.get("visibility") or "room")
    text = str(body.get("text") or "").strip()[:500]
    ui_context = body.get("ui_context") or {}
    # session_id sent by the frontend reflects the panel's active session, bypassing user.json race conditions
    client_session_id = str(body.get("session_id") or "").strip()

    if not text:
        return jsonify({"error": "text erforderlich"}), 400

    if visibility == "local_only":
        return jsonify({"error": "local_only Nachrichten werden am Hub abgelehnt"}), 422

    # UI-context tick from the visual snake frontend — update state + spawn proactive guide reply
    if visibility == "system" and text.startswith("[ui-tick]"):
        _ui_snap = str((ui_context or {}).get("ui_snapshot") or "").strip()[:500]
        if snake_id and _ui_snap:
            existing = _snake_ui_state.get(snake_id) or {}
            _snake_ui_state[snake_id] = {
                **existing,
                "route": str((ui_context or {}).get("route") or existing.get("route") or ""),
                "visible_waypoints": list((ui_context or {}).get("visible_waypoints") or existing.get("visible_waypoints") or [])[:30],
                "ui_snapshot": _ui_snap,
                "updated_at": time.time(),
            }
            # Persist the incoming tick in the ananta-visual session for later analysis
            _append_visual_user_tick(ui_snapshot=_ui_snap, snake_id=snake_id)
            # VG-053: submit to ThreadPoolExecutor instead of daemon thread
            _VISUAL_GUIDE_EXECUTOR.submit(_spawn_visual_reply, _ui_snap, snake_id)
        return jsonify({"ok": True, "id": str(body.get("id") or "")}), 202

    # Region-explain event: user drew a selection. Log it and spawn AI explanations.
    # The AI returns __GUIDE__: steps with original pixel coordinates + explanation bubbles.
    if visibility == "system" and text.startswith("[region-explain]"):
        # Candidate selection from a multi-candidate guide is logged but does not
        # trigger another LLM round-trip; the chosen candidate already contains steps.
        if text.startswith("[region-explain] candidate:"):
            _append_room_ai_message(
                text=text[:500],
                session_id=_VISUAL_SESSION_ID,
                visibility="system",
                sender_id="browser",
            )
            return jsonify({"ok": True, "id": str(body.get("id") or "")}), 202

        _append_room_ai_message(
            text=text[:500],
            session_id=_VISUAL_SESSION_ID,
            visibility="system",
            sender_id="browser",
        )
        _region_steps = list((ui_context or {}).get("region_steps") or [])
        _region_route = str((ui_context or {}).get("route") or "").strip()
        if _region_steps:
            # VG-053: submit to ThreadPoolExecutor instead of daemon thread
            _VISUAL_GUIDE_EXECUTOR.submit(_spawn_region_explain_reply, _region_steps, _region_route, snake_id)
        return jsonify({"ok": True, "id": str(body.get("id") or "")}), 202

    if channel_type not in _VALID_CHANNEL_TYPES:
        return jsonify({"error": f"ungültiger channel_type: {channel_type}"}), 422

    # Backend-side guard: ananta-visual is a read-only log.
    # Only browser-side [ui-tick] and [region-explain] system messages are allowed.
    _allowed_visual = visibility == "system" and (
        text.startswith("[ui-tick]") or text.startswith("[region-explain]")
    )
    if client_session_id == "ananta-visual" and not _allowed_visual:
        return jsonify({"error": "ananta-visual ist eine Read-only-Log-Session"}), 403

    msg: dict[str, Any] = {
        "id": str(body.get("id") or str(uuid.uuid4())),
        "created_at": time.time(),
        "channel_id": f"{channel_type}:main" if channel_type == "room" else f"{channel_type}:{snake_id}",
        "channel_type": channel_type,
        "sender_id": snake_id,
        "sender_kind": "user",
        "target_ids": list(body.get("target_ids") or []),
        "text": text,
        "visibility": visibility,
        "delivery_state": "received",
        "policy_decision_ref": None,
        "session_id": client_session_id,
    }

    if channel_type == "room":
        global _room_messages  # noqa: PLW0602
        existing_ids = {m["id"] for m in _room_messages}
        if msg["id"] not in existing_ids:
            _room_messages.append(msg)
            if len(_room_messages) > _MAX_ROOM_MSGS:
                _room_messages = _room_messages[-_MAX_ROOM_MSGS:]
            _spawn_ai_chat_reply(user_text=text, snake_id=snake_id, ui_context=ui_context, client_session_id=client_session_id)
    elif channel_type == "direct":
        target_ids = msg["target_ids"]
        if not target_ids:
            return jsonify({"error": "target_ids erforderlich für direct"}), 422
        target_id = str(target_ids[0])
        if target_id not in _snakes:
            return jsonify({"error": f"Ziel-Snake unbekannt: {target_id}"}), 422
        inbox = _chat_messages.setdefault(target_id, [])
        existing_ids = {m["id"] for m in inbox}
        if msg["id"] not in existing_ids:
            inbox.append(msg)
            if len(inbox) > _MAX_CHAT_MSGS:
                _chat_messages[target_id] = inbox[-_MAX_CHAT_MSGS:]
    else:
        return jsonify({"error": f"channel_type {channel_type} nicht unterstützt"}), 422

    return jsonify({"ok": True, "id": msg["id"]}), 202


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["GET"])
def chat_receive(snake_id: str):
    """GET /snakes/<id>/chat/messages?since=<cursor> -- Chat-Nachrichten abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404

    since_str = request.args.get("since", "")
    since: float = float(since_str) if since_str else 0.0
    requested_session_id = str(request.args.get("session_id") or "").strip()

    direct = [m for m in _chat_messages.get(snake_id, []) if float(m.get("created_at") or 0) > since]
    room = [
        m for m in _room_messages
        if float(m.get("created_at") or 0) > since
        and m.get("sender_id") != snake_id
        and (
            not requested_session_id
            or str(m.get("session_id") or "") == requested_session_id
        )
    ]

    all_msgs = sorted(direct + room, key=lambda m: float(m.get("created_at") or 0))

    if direct:
        delivered_ids = {m["id"] for m in direct}
        _chat_messages[snake_id] = [m for m in _chat_messages.get(snake_id, []) if m["id"] not in delivered_ids]

    new_cursor = str(time.time()) if all_msgs else since_str

    return jsonify({"messages": all_msgs, "cursor": new_cursor}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/cancel", methods=["POST"])
def chat_cancel(snake_id: str):
    """POST /snakes/<id>/chat/cancel -- Laufenden AI-Snake-Chat abbrechen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    keys = ("room", "snake_ask", snake_id)
    cancelled_keys = cancel_chat(keys)
    legacy_cancelled = False
    for key in keys:
        event = _SCAN_CANCELS.get(key)
        if event:
            event.set()
            legacy_cancelled = True
        full_scan_event = _FULL_SCAN_CANCELS.get(key)
        if full_scan_event:
            full_scan_event.set()
            legacy_cancelled = True
    return jsonify({"ok": True, "cancelled": bool(cancelled_keys) or legacy_cancelled, "keys": cancelled_keys}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/ack", methods=["POST"])
def chat_ack(snake_id: str):
    """POST /snakes/<id>/chat/ack -- Gelesene Nachrichten bestätigen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


@snakes_bp.route("/snakes/<snake_id>/events/stream", methods=["GET"])
def snake_events_stream(snake_id: str):
    """GET /snakes/<id>/events/stream -- Server-Sent Events for snake events.

    Streams typed events generated by backend components (e.g. visual guide
    actions, candidate lists).  Polling remains available as a fallback.
    The stream sends a keep-alive comment every ~15s and closes gracefully
    when the snake is deleted or the client disconnects.
    """
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    # Auth: same token as snake registration, but SSE uses EventSource which
    # cannot send custom headers.  Accept token via query parameter.
    token = request.args.get("token", "")
    if not token or not secrets.compare_digest(str(snake.get("token") or ""), token):
        return jsonify({"error": "Ungültiger Token"}), 401

    def _event_stream():
        while True:
            event = get_snake_event(snake_id, timeout=15.0)
            if event is not None:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                # Keep-alive to prevent proxies from closing idle connections
                yield ":keep-alive\n\n"
            if _snakes.get(snake_id) is not snake:
                # Snake was deleted/disconnected
                break

    return Response(
        _event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@snakes_bp.route("/snakes/<snake_id>/ui-state", methods=["PUT"])
def snake_ui_state_push(snake_id: str):
    """PUT /snakes/<id>/ui-state -- aktuellen UI-Zustand des Browsers speichern."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    route = str(body.get("route") or "").strip()
    visible_waypoints = [str(w) for w in (body.get("visible_waypoints") or []) if w][:30]
    active_surface = str(body.get("active_surface") or "").strip()
    ui_snapshot = str(body.get("ui_snapshot") or "").strip()[:500]
    _snake_ui_state[snake_id] = {
        "route": route,
        "visible_waypoints": visible_waypoints,
        "active_surface": active_surface,
        "ui_snapshot": ui_snapshot,
        "updated_at": time.time(),
    }
    return jsonify({"ok": True})


@snakes_bp.route("/worker-context", methods=["POST"])
def worker_context():
    """POST /worker-context -- CWFH-009: Build WorkerContextHandoffV3 from a question.

    Accepts:
      {
        "question": str,
        "output_dir": str,
        "memory_context": str?,
        "manifest_hash": str?,
        "depth": str?,
        "workspace_root": str?,
        "max_candidates": int?
      }

    Returns WorkerContextHandoffV3 dict with candidate_files + context_files.
    """
    if not _is_local_request():
        auth = _optional_user_auth()
        if not auth:
            return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = str(body.get("question") or "").strip()[:2000]
    output_dir = str(body.get("output_dir") or "").strip()
    memory_context = str(body.get("memory_context") or "").strip() or None
    manifest_hash = str(body.get("manifest_hash") or "").strip() or None
    depth = str(body.get("depth") or "").strip() or None
    workspace_root = str(body.get("workspace_root") or "").strip() or None
    max_candidates = int(body.get("max_candidates") or 40)

    if not question:
        return jsonify({"error": "question required"}), 400
    if not output_dir:
        return jsonify({"error": "output_dir required"}), 400

    try:
        from worker.retrieval.codecompass_candidate_resolver import (
            CodeCompassCandidateResolver, ResolverConfig,
        )
        from agent.services.context_file_reader_service import (
            ContextFileReaderService, FileReadPolicy,
        )
        from agent.services.worker_contract_service import get_worker_contract_service
        from agent.services.worker_context_handoff_diagnostics_service import (
            get_worker_context_handoff_diagnostics_service,
        )

        resolver = CodeCompassCandidateResolver(max_candidates=max(1, min(max_candidates, 100)))
        mode = ResolverConfig.from_env()
        candidates = resolver.resolve(
            question=question,
            output_dir=output_dir,
            memory_context=memory_context,
            manifest_hash=manifest_hash,
            mode=mode,
        )

        policy = FileReadPolicy(workspace_root=workspace_root or output_dir)
        reader = ContextFileReaderService(policy=policy)
        context_files = reader.read_required_files(candidates)

        handoff = get_worker_contract_service().build_worker_context_handoff_v3(
            question=question,
            candidate_files=candidates,
            context_files=context_files,
            depth=depth,
            memory_context=memory_context,
            manifest_hash=manifest_hash,
        )
        handoff["diagnostics"] = get_worker_context_handoff_diagnostics_service().summarize(handoff)
        return jsonify(handoff), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("worker-context failed: %s", exc, exc_info=True)
        return jsonify({"error": f"worker-context error: {str(exc)[:200]}"}), 500


@snakes_bp.route("/snake/ask", methods=["POST"])
def snake_ask():
    """POST /snake/ask -- Synchrone AI-Antwort für den TUI ananta-worker Modus.

    Akzeptiert v1 ({question, context, depth}) und v2 ({question, context, depth, memory_context}).
    Optionales Feld "debug": true gibt trace-Infos zurück.
    Antwortet mit {"answer": "..."}. Routet über einen registrierten Worker-Prozess;
    fällt auf direkten LMStudio-Aufruf zurück falls kein Worker verfügbar.
    """
    if not _is_local_request():
        auth = _optional_user_auth()
        if not auth:
            return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    question = str(body.get("question") or "").strip()[:1000]
    debug = bool(body.get("debug"))
    trace_only = bool(body.get("trace_only"))
    limits = SnakeAskLimits.from_payload(body)
    retrieval_config_overrides = _snake_retrieval_config_overrides(body)
    request_model = str(body.get("model") or "").strip() or None
    if not question:
        return jsonify({"error": "question erforderlich"}), 400

    if trace_only:
        dry = _snake_retrieval_dry_run(
            question,
            retrieval_config_overrides=retrieval_config_overrides,
            top_k=limits.rag_top_k,
        )
        return jsonify({"trace_only": True, "rag_why": dry}), 200

    rag_trace: dict[str, Any] = {}
    domain_scope_info: dict[str, Any] = {}
    domain_hint = str(dict(retrieval_config_overrides or {}).get("chat_retrieval_domain_hint") or "") or None
    context = str(body.get("context") or "").strip()[:limits.context_chars]
    if context:
        grounded_prompt = f"{question}\n\nKontext:\n{context}"
        rag_trace["source"] = "client_provided"
        rag_trace["context_chars"] = len(context)
        if debug or retrieval_config_overrides:
            rag_trace["retrieval_profile"] = _resolve_snake_retrieval_profile_trace(
                question,
                retrieval_config_overrides=retrieval_config_overrides,
            )
    else:
        grounded_prompt, has_context, context_summary, domain_scope_info, _chunks = _build_grounded_snake_prompt(
            question,
            limits=limits,
            retrieval_config_overrides=retrieval_config_overrides,
        )
        rag_trace["source"] = "hub_rag"
        rag_trace["has_context"] = has_context
        rag_trace["summary"] = context_summary
        if debug or retrieval_config_overrides:
            rag_trace["retrieval_profile"] = _resolve_snake_retrieval_profile_trace(
                question,
                retrieval_config_overrides=retrieval_config_overrides,
            )
    rag_trace["limits"] = {
        "context_chars": limits.context_chars,
        "answer_chars": limits.answer_chars,
        "max_tokens": limits.max_tokens,
        "rag_top_k": limits.rag_top_k,
        "answer_overflow_policy": limits.answer_overflow_policy,
        "never_truncate_answers": limits.never_truncate_answers,
    }

    provider, hub_model, api_base = _resolve_ai_snake_chat_provider()
    model = request_model or hub_model

    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import _is_full_scan_intent, _is_rag_iterative_intent

        _eff_cfg = _current_config()
        _eff_cfg.update(dict(retrieval_config_overrides or {}))
        # Resolve active session's system_prompt and apply session-level setting overrides
        _active_session_prompt: str | None = None
        _active_sid = ""
        try:
            from client_surfaces.operator_tui.config.user_config_manager import get_manager as _get_mgr
            _stored = _get_mgr().load()
            _active_sid = str(_stored.get("chat_active_session_id") or "").strip()
            if _active_sid:
                for _sess in (_stored.get("chat_sessions") or []):
                    if str(_sess.get("id") or "") == _active_sid:
                        _active_session_prompt = str(_sess.get("system_prompt") or "").strip() or None
                        break
            if _active_sid == "ananta-settings":
                _eff_cfg = {
                    **_eff_cfg,
                    "chat_architecture_analysis_mode": False,
                    "chat_retrieval_profile": "none",
                    "chat_use_codecompass": False,
                    "chat_code_questions_repo_first": False,
                    "chat_include_local_project": False,
                }
        except Exception:
            pass
        # Always override system_prompt for built-in sessions with the canonical DEFAULT_SESSIONS value
        try:
            from client_surfaces.operator_tui.chat_state import DEFAULT_SESSIONS as _DS
            for _ds in _DS:
                if str(_ds.get("id") or "") == _active_sid:
                    _cp = str(_ds.get("system_prompt") or "").strip()
                    if _cp:
                        _active_session_prompt = _cp
                    break
        except Exception:
            pass
        if _is_rag_iterative_intent(_eff_cfg):
            _cancel_keys = ["snake_ask"]
            _cancel_event = register_chat_cancel(_cancel_keys)
            try:
                answer, worker_trace = _worker_chat_rag_iterative(
                    question,
                    provider=provider,
                    model=model,
                    limits=limits,
                    cancel_event=_cancel_event,
                    system_prompt=_active_session_prompt,
                )
            finally:
                unregister_chat_cancel(_cancel_keys, _cancel_event)
            if worker_trace.get("cancelled") or (worker_trace.get("tool_loop") or {}).get("cancelled"):
                resp = {
                    "answer": "Anfrage abgebrochen.",
                    "path": "rag_iterative",
                    "context_summary": "rag_iterative: abgebrochen",
                    "cancelled": True,
                    **domain_scope_info,
                }
                if debug:
                    resp["trace"] = {"worker": worker_trace}
                return jsonify(resp), 200
            if answer:
                _tl = worker_trace.get("tool_loop") or {}
                if _tl or worker_trace.get("available_files"):
                    _avail = worker_trace.get("available_files") or []
                    _tc_made = _tl.get("tool_calls_made", 0)
                    file_names = ", ".join(str(p).split("/")[-1] for p in _avail[:6])
                    if len(_avail) > 6:
                        file_names += f" +{len(_avail) - 6}"
                    summary = f"rag_iterative: {_tc_made} Tool-Calls, {len(_avail)} Dateien verfügbar" + (f" ({file_names})" if file_names else "")
                else:
                    batches_done = worker_trace.get("batches_completed", 0)
                    files_found = worker_trace.get("files_resolved", 0)
                    file_list = worker_trace.get("file_list") or []
                    file_names = ", ".join(str(p).split("/")[-1] for p in file_list[:6])
                    if len(file_list) > 6:
                        file_names += f" +{len(file_list) - 6}"
                    summary = f"rag_iterative: {batches_done} Batches, {files_found} Dateien" + (f" ({file_names})" if file_names else "")
                answer = _fit_answer_to_chars(
                    answer,
                    limit=limits.answer_chars,
                    provider=provider,
                    model=model,
                    timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
                    overflow_policy=limits.answer_overflow_policy,
                    never_truncate=limits.never_truncate_answers,
                )
                resp: dict[str, Any] = {"answer": answer, "path": "rag_iterative", "context_summary": summary, **domain_scope_info}
                if debug:
                    resp["trace"] = {"worker": worker_trace}
                return jsonify(resp), 200
        elif _is_full_scan_intent(question, "", _eff_cfg):
            answer, worker_trace = _worker_chat_full_scan(question, provider=provider, model=model, limits=limits, cancel_key="snake_ask")
            if answer:
                files_found = worker_trace.get("files_found", 0)
                batches_done = worker_trace.get("batches_completed", 0)
                summary = f"full_scan: {batches_done} Batches, {files_found} Quelldateien"
                answer = _fit_answer_to_chars(
                    answer,
                    limit=limits.answer_chars,
                    provider=provider,
                    model=model,
                    timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
                    overflow_policy=limits.answer_overflow_policy,
                    never_truncate=limits.never_truncate_answers,
                )
                resp: dict[str, Any] = {"answer": answer, "path": "full_scan", "context_summary": summary, **domain_scope_info}
                if debug:
                    resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
                elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
                    resp["trace"] = {"rag": rag_trace}
                return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).debug("full_scan routing failed, falling back: %s", exc)

    answer, worker_trace = _worker_propose(
        grounded_prompt,
        model,
        provider=provider,
        limits=limits,
        retrieval_profile_trace=rag_trace.get("retrieval_profile") if isinstance(rag_trace.get("retrieval_profile"), dict) else None,
        worker_picker=_pick_worker_for_ask,
        model_resolver=_resolve_lmstudio_model_for_worker,
    )
    if answer:
        resp = {"answer": answer, "path": "worker", **domain_scope_info}
        if debug:
            resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
        elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
            resp["trace"] = {"rag": rag_trace}
        return jsonify(resp), 200

    try:
        _, _, api_base = _resolve_ai_snake_chat_provider()
        timeout = min(int(getattr(settings, "http_timeout", 120) or 120), 180)
        raw = generate_text(
            prompt=_with_answer_budget_instruction(
                grounded_prompt,
                limits.answer_chars,
                policy=limits.answer_overflow_policy,
            ),
            provider=provider,
            model=model,
            base_url=api_base,
            history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
            max_output_tokens=limits.max_tokens,
            timeout=timeout,
        )
        text = str(raw or "").strip()
        text = _fit_answer_to_chars(
            text,
            limit=limits.answer_chars,
            provider=provider,
            model=model,
            timeout=timeout,
            overflow_policy=limits.answer_overflow_policy,
            never_truncate=limits.never_truncate_answers,
            text_generator=generate_text,
        )
        if not text:
            return jsonify({"error": "Keine Antwort generiert"}), 503
        resp = {"answer": text, "path": "hub_direct", **domain_scope_info}
        if debug:
            resp["trace"] = {
                "rag": rag_trace,
                "worker": worker_trace,
                "fallback_reason": "worker_empty",
                "full_scan": {
                    "status": "not_run",
                    "reason": "hub_direct_fallback",
                    "analysis_mode": (rag_trace.get("retrieval_profile") or {}).get("analysis_mode"),
                },
            }
        elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
            resp["trace"] = {
                "rag": rag_trace,
                "fallback_reason": "worker_empty",
            }
        return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("snake-ask failed: %s", exc)
        return jsonify({"error": f"LLM-Fehler: {str(exc)[:120]}"}), 503


# ── Trace API ──────────────────────────────────────────────────────────────────


@snakes_bp.route("/snakes/<snake_id>/chat/traces", methods=["GET"])
def chat_traces_list(snake_id: str):
    """GET /snakes/<id>/chat/traces -- Liste der Traces für diese Snake."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store
        store = get_trace_store()
        limit = min(int(request.args.get("limit") or 20), 100)
        traces = store.list_traces(snake_id=snake_id, limit=limit)
        return jsonify({"traces": traces, "snake_id": snake_id}), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_traces_list failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500


@snakes_bp.route("/snakes/<snake_id>/chat/traces/<trace_id>", methods=["GET"])
def chat_trace_detail(snake_id: str, trace_id: str):
    """GET /snakes/<id>/chat/traces/<trace_id> -- Trace-Metadaten abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store
        store = get_trace_store()
        trace = store.get_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Trace nicht gefunden"}), 404
        if trace.get("snake_id") and trace["snake_id"] != snake_id:
            return jsonify({"error": "Trace gehört nicht zu dieser Snake"}), 403
        return jsonify({"trace": trace}), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_trace_detail failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500


@snakes_bp.route("/snakes/<snake_id>/chat/traces/<trace_id>/events", methods=["GET"])
def chat_trace_events(snake_id: str, trace_id: str):
    """GET /snakes/<id>/chat/traces/<trace_id>/events?since_seq=0 -- Events inkrementell abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    try:
        from agent.routes.ai_snake_trace_store import get_trace_store
        store = get_trace_store()
        trace = store.get_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Trace nicht gefunden"}), 404
        if trace.get("snake_id") and trace["snake_id"] != snake_id:
            return jsonify({"error": "Trace gehört nicht zu dieser Snake"}), 403
        since_seq = max(0, int(request.args.get("since_seq") or 0))
        events = store.get_events(trace_id, since_seq=since_seq)
        return jsonify({
            "trace_id": trace_id,
            "current_status": trace.get("status", "unknown"),
            "latest_seq": trace.get("latest_seq", -1),
            "events": events,
        }), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("chat_trace_events failed: %s", exc)
        return jsonify({"error": "Interner Fehler"}), 500
