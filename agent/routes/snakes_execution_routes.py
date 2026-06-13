"""Snake execution endpoints — chat API, ask, worker-context."""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, has_app_context, jsonify, request

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.rag_service import get_rag_service

from .snakes import (
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

_SNAKE_RETRIEVAL_CONFIG_KEYS = frozenset({
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
})


@dataclass(frozen=True, slots=True)
class SnakeAskLimits:
    context_chars: int = 4000
    answer_chars: int = 2200
    max_tokens: int | None = None
    rag_top_k: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SnakeAskLimits":
        return cls(
            context_chars=_bounded_optional_int(payload.get("context_chars"), default=4000, minimum=500, maximum=20000),
            answer_chars=_bounded_optional_int(payload.get("answer_chars"), default=2200, minimum=600, maximum=12000),
            max_tokens=_bounded_optional_int(payload.get("max_tokens"), default=None, minimum=100, maximum=8000),
            rag_top_k=_bounded_optional_int(payload.get("rag_top_k"), default=None, minimum=1, maximum=120),
        )


def _bounded_optional_int(value: Any, *, default: int | None, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _background_threads_disabled() -> bool:
    return bool(
        (has_app_context() and bool(getattr(current_app, "testing", False)))
        or str(getattr(settings, "role", "")).strip().lower() == "test"
        or os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


def _resolve_ai_snake_chat_provider() -> tuple[str, str | None]:
    provider = "lmstudio"
    model: str | None = None
    try:
        from agent.routes.ai_snake_config import _current_config

        cfg = _current_config()
        backend = str(cfg.get("chat_backend") or "").strip().lower()
        fallback = str(cfg.get("chat_backend_fallback") or "").strip().lower()
        configured_model = str(cfg.get("chat_backend_model") or "").strip() or None
        if configured_model:
            model = configured_model
        if backend == "lmstudio":
            provider = "lmstudio"
        elif backend in {"ananta-worker", "opencode", "hermes"}:
            provider = "lmstudio" if fallback in {"", "none", "lmstudio"} else "lmstudio"
    except Exception:
        pass
    return provider, model


def _snake_retrieval_config_overrides(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("retrieval_config")
    if not isinstance(raw, dict):
        return {}
    overrides: dict[str, Any] = {}
    for key in _SNAKE_RETRIEVAL_CONFIG_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            overrides[key] = value
        elif isinstance(value, str):
            overrides[key] = value.strip()
    return overrides


def _build_local_repo_fallback_context(prompt: str) -> str:
    text = str(prompt or "").lower()
    repo_root = Path(getattr(settings, "rag_repo_root", ".")).resolve()
    if "_build_grounded_snake_prompt" in text or "snakes.py" in text:
        snakes_file = repo_root / "agent" / "routes" / "snakes.py"
        if snakes_file.exists():
            try:
                lines = snakes_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return ""
            for idx, line in enumerate(lines):
                if "def _build_grounded_snake_prompt" in line:
                    start = max(0, idx - 4)
                    end = min(len(lines), idx + 24)
                    return "\n".join(lines[start:end]).strip()
    if "agent/routes" in text or "routes" in text:
        routes_dir = repo_root / "agent" / "routes"
        if routes_dir.exists() and routes_dir.is_dir():
            names = sorted(path.name for path in routes_dir.glob("*.py") if path.is_file())
            if names:
                return "Dateien in agent/routes:\n" + "\n".join(f"- {name}" for name in names[:24])
    return ""


def _domain_scope_response(domain_scope: object | None, bundle_domain_scope: dict | None) -> dict[str, Any]:
    """Build a serialisable domain_scope response fragment for snake ask."""
    if domain_scope is None:
        return {}
    from agent.codecompass.domain_scope import ResolvedDomainScope
    if isinstance(domain_scope, ResolvedDomainScope) and domain_scope.active:
        info = domain_scope.as_dict()
        if bundle_domain_scope and isinstance(bundle_domain_scope, dict):
            info.update({
                k: bundle_domain_scope[k]
                for k in ("active_domain_ids", "filter_stats", "guidance")
                if k in bundle_domain_scope
            })
        return {"domain_scope": info}
    return {}


def _resolve_domain_scope_for_chat(domain_hint: str | None) -> object | None:
    """CCRDS-014: resolve a ``domain:``-prefixed hint to a DomainScope or None.

    Returns None when the feature is disabled, the hint is unprefixed, or
    the hint is empty — the caller then proceeds without hard scoping.
    """
    if not domain_hint:
        return None
    if not str(getattr(settings, "codecompass_domain_scope_enabled", False)).strip().lower() in {"1", "true", "yes"}:
        return None
    try:
        from agent.codecompass.domain_scope_resolver import (
            DomainScopeResolver,
            scope_from_domain_hint,
        )
        scope = scope_from_domain_hint(
            domain_hint,
            enabled=True,
            strict=bool(getattr(settings, "codecompass_scope_strict_mode", True)),
        )
        if scope is None:
            return None
        resolver = DomainScopeResolver(
            repo_root=Path(__file__).resolve().parents[2],
            artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
            descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
        )
        return resolver.resolve(scope)
    except Exception:
        return None


def _build_grounded_snake_prompt(
    user_text: str,
    *,
    limits: SnakeAskLimits | None = None,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> tuple[str, bool, str, dict[str, Any]]:
    prompt = str(user_text or "").strip()
    if not prompt:
        return prompt, False, "", {}
    effective_limits = limits or SnakeAskLimits()
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))

        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(prompt, cfg, domain_hint=domain_hint, feature_flag=feature_flag)

        # CCRDS-014: resolve runtime domain scope from domain_hint
        domain_scope = _resolve_domain_scope_for_chat(domain_hint)

        bundle, grounded = get_rag_service().build_execution_context(
            prompt,
            task_kind="research",
            retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
            source_types=profile.source_types or None,
            max_chunks=effective_limits.rag_top_k,
            retrieval_profile=profile.as_dict(),
            domain_scope=domain_scope,
        )
        chunks = list(bundle.get("chunks") or [])
        domain_scope_info = _domain_scope_response(domain_scope, bundle.get("domain_scope"))
        if chunks:
            src_type_counts: dict[str, int] = {}
            for chunk in chunks:
                metadata = dict((chunk or {}).get("metadata") or {})
                st = str(metadata.get("source_type") or (chunk or {}).get("engine") or "unknown").strip().lower() or "unknown"
                src_type_counts[st] = int(src_type_counts.get(st, 0)) + 1
            logging.getLogger(__name__).info(
                "ai_snake_retrieval_profile_selected profile_id=%s domain=%s intent=%s feature_flag=%s source_type_counts=%s warnings=%s",
                profile.profile_id,
                profile.domain,
                profile.intent,
                profile.feature_flag,
                src_type_counts,
                list(profile.warnings),
            )
            summary_parts = [f"{k}:{v}" for k, v in sorted(src_type_counts.items())]
            summary = f"Kontext: {len(chunks)} Treffer ({', '.join(summary_parts)}) [{profile.profile_id}]"
            return grounded, True, summary, domain_scope_info
    except Exception as exc:
        logging.getLogger(__name__).debug("ai_snake_retrieval_profile_failed: %s", exc)
        pass
    local_fallback = _build_local_repo_fallback_context(prompt)
    if local_fallback:
        grounded = (
            f"{prompt}\n\n"
            "Lokaler Projektkontext (Fallback, wenn RAG leer ist):\n"
            f"{local_fallback}"
        )
        return prompt, True, "Kontext: 1 Treffer (repo_fallback:1)", {}
    return prompt, False, "Kontext: 0 Treffer", {}


def _resolve_snake_retrieval_profile_trace(
    user_text: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None
        profile = resolve_profile(str(user_text or ""), cfg, domain_hint=domain_hint, feature_flag=feature_flag)

        # CCRDS-006/014: show the soft profile domain and the hard runtime
        # scope as separate trace fields. The scope is only resolved when
        # the feature flag is on and the hint uses the `domain:` prefix.
        runtime_domain_scope: dict[str, Any] = {"active": False}
        try:
            from agent.codecompass.domain_scope_resolver import (
                DomainScopeResolver,
                scope_from_domain_hint,
            )

            scope = scope_from_domain_hint(
                domain_hint,
                enabled=bool(getattr(settings, "codecompass_domain_scope_enabled", False)),
                strict=bool(getattr(settings, "codecompass_scope_strict_mode", True)),
            )
            if scope is not None:
                resolver = DomainScopeResolver(
                    repo_root=Path(__file__).resolve().parents[2],
                    artifact_path=str(getattr(settings, "codecompass_domain_artifact_path", "") or "") or None,
                    descriptor_root=str(getattr(settings, "codecompass_domain_descriptor_root", "") or "") or None,
                )
                runtime_domain_scope = resolver.resolve(scope).as_dict()
        except Exception as scope_exc:
            runtime_domain_scope = {"active": False, "error": str(scope_exc)[:120]}

        return {
            "profile_id": profile.profile_id,
            "retrieval_profile_domain": profile.domain,
            "runtime_domain_scope": runtime_domain_scope,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "output_intent": profile.output_intent,
            "coverage_policy": profile.coverage_policy,
            "summary_policy": profile.summary_policy,
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _snake_retrieval_dry_run(
    question: str,
    *,
    retrieval_config_overrides: dict[str, Any] | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    """RWY-005: run retrieval for *question* without calling an LLM.

    Returns profile metadata, resolver scope flags, candidate counts per
    source type, top-5 candidate paths, and preset hints.
    """
    result: dict[str, Any] = {}
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import resolve_profile
        from worker.retrieval.codecompass_candidate_resolver import ResolverConfig

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(question, cfg, domain_hint=domain_hint, feature_flag=feature_flag)
        result["retrieval_profile"] = {
            "profile_id": profile.profile_id,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "feature_flag": profile.feature_flag,
            "trigger_mode": str(cfg.get("chat_codecompass_trigger_mode") or "auto").strip().lower(),
            "selected_by": profile.selected_by,
            "reasons": list(profile.reasons),
            "source_types": list(profile.source_types),
            "source_type_weights": dict(profile.source_type_weights),
            "negative_source_patterns": list(profile.negative_source_patterns),
            "warnings": list(profile.warnings),
        }

        scope = ResolverConfig.from_env()
        result["resolver_scope"] = {
            "include_source": scope.include_source,
            "include_test_paths": scope.include_test_paths,
            "include_docs": scope.include_docs,
            "include_workflows": scope.include_workflows,
            "include_third_party": scope.include_third_party,
            "include_xml_nodes": scope.include_xml_nodes,
        }

        try:
            bundle, _ = get_rag_service().build_execution_context(
                question,
                task_kind="research",
                retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
                source_types=profile.source_types or None,
                max_chunks=max(8, min(top_k, 40)),
                retrieval_profile=profile.as_dict(),
            )
            chunks = list(bundle.get("chunks") or [])
            src_counts: dict[str, int] = {}
            top_sources: list[dict[str, Any]] = []
            for ch in chunks:
                meta = dict((ch or {}).get("metadata") or {})
                st = str(meta.get("source_type") or (ch or {}).get("engine") or "unknown").lower()
                src_counts[st] = src_counts.get(st, 0) + 1
                if len(top_sources) < 5:
                    path = str(meta.get("file_path") or meta.get("path") or (ch or {}).get("path") or "").strip()
                    score = float((ch or {}).get("score") or meta.get("score") or 0.0)
                    if path:
                        top_sources.append({"path": path, "source_type": st, "score": round(score, 3)})
            result["candidate_counts"] = {
                "total": len(chunks),
                "by_source_type": src_counts,
            }
            result["top_sources"] = top_sources
            result["degraded_channels"] = []
        except Exception as exc:
            result["retrieval_error"] = str(exc)[:200]
            result["candidate_counts"] = {"total": 0, "by_source_type": {}}
            result["top_sources"] = []
            result["degraded_channels"] = [str(exc)[:120]]

        q_lower = question.lower()
        hints: list[str] = []
        if any(w in q_lower for w in ("readme", "docs", "doku", "architektur", "todo", "notiz")) and not scope.include_docs:
            hints.append("Tipp: docs/artifact deaktiviert \u2192 :config chat_retrieval_profile docs_first")
        if any(w in q_lower for w in ("test", "spec", "pytest", "unittest")) and not scope.include_test_paths:
            hints.append("Tipp: tests deaktiviert \u2192 ANANTA_CODECOMPASS_INCLUDE_TEST_PATHS=1 oder :config code_with_tests")
        if any(w in q_lower for w in ("workflow", "blueprint", "ops", "runbook")) and not scope.include_workflows:
            hints.append("Tipp: workflows deaktiviert \u2192 ANANTA_CODECOMPASS_INCLUDE_WORKFLOWS=1 oder :config ops")
        result["preset_hints"] = hints

    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _append_room_ai_message(*, text: str) -> None:
    if not text:
        return
    msg: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "channel_id": "room:main",
        "channel_type": "room",
        "sender_id": "ai-snake",
        "sender_kind": "assistant",
        "target_ids": [],
        "text": text[:6000],
        "visibility": "room",
        "delivery_state": "received",
        "policy_decision_ref": None,
    }
    global _room_messages
    _room_messages.append(msg)
    if len(_room_messages) > _MAX_ROOM_MSGS:
        _room_messages = _room_messages[-_MAX_ROOM_MSGS:]


from .snakes_full_scan import worker_chat_full_scan as _worker_chat_full_scan


def _trace_feature_enabled() -> bool:
    try:
        from agent.routes.ai_snake_config import _current_config
        cfg = _current_config()
        return bool(cfg.get("ai_snake_trace_enabled", True))
    except Exception:
        return True


def _spawn_ai_chat_reply(*, user_text: str, snake_id: str | None = None) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        rec = None
        store = None
        trace_id = None
        try:
            if _trace_feature_enabled():
                from agent.routes.ai_snake_trace_store import get_trace_store, TraceRecorder
                store = get_trace_store()
                trace_id = store.new_trace(snake_id=snake_id)
                rec = TraceRecorder(store, trace_id)
                rec.event(
                    "request_received", "Anfrage empfangen",
                    status="completed",
                    summary=f"Prompt: {prompt[:120]}{'\u2026' if len(prompt) > 120 else ''}",
                )

            provider, model = _resolve_ai_snake_chat_provider()
            if rec:
                rec.event("config_loaded", "Provider-Konfiguration geladen", status="completed",
                          details={"provider": provider, "model": model})

            try:
                from agent.routes.ai_snake_config import _current_config
                from agent.services.retrieval_profile_service import _is_full_scan_intent
                _cfg = _current_config()
                if _is_full_scan_intent(prompt, "", _cfg):
                    if rec:
                        rec.event("full_scan_detected", "Full-Scan erkannt", status="running",
                                  summary="Architektur-Analyse wird gestartet")
                    t0 = time.time()
                    answer, scan_trace = _worker_chat_full_scan(prompt, provider=provider, model=model, cancel_key="room")
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
                    if len(answer) > 5800:
                        answer = answer[:5800].rstrip() + "\n\n[gekuerzt]"
                    if rec:
                        rec.event("answer_postprocessed", "Antwort aufbereitet", status="completed",
                                  summary=f"{len(answer)} Zeichen")
                    _append_room_ai_message(text=f"{answer}\n\n[{scan_summary}]")
                    if rec:
                        rec.event("chat_message_written", "Nachricht in Raum geschrieben", status="completed")
                    if store and trace_id:
                        store.complete_trace(trace_id)
                    return
            except Exception as exc:
                logging.getLogger(__name__).debug("full_scan check failed, falling back: %s", exc)

            if rec:
                rec.event("retrieval_profile_selected", "Retrieval-Profil wird aufgel\u00f6st", status="running")

            retrieval_start = time.time()
            if rec:
                rec.event("codecompass_retrieval_started", "CodeCompass Retrieval gestartet", status="running",
                          input_preview=prompt[:300])

            grounded_prompt, has_context, context_summary, _domain_info = _build_grounded_snake_prompt(prompt)

            retrieval_ms = (time.time() - retrieval_start) * 1000
            if rec:
                rec.event(
                    "codecompass_retrieval_completed", "CodeCompass Retrieval abgeschlossen",
                    status="completed" if has_context else "skipped",
                    summary=context_summary,
                    duration_ms=retrieval_ms,
                    details={"has_context": has_context, "grounded_chars": len(grounded_prompt)},
                )
                rec.event("prompt_built", "Prompt aufgebaut", status="completed",
                          summary=f"{len(grounded_prompt)} Zeichen",
                          details={"context_summary": context_summary})

            q = prompt.lower()
            asks_for_concrete_local_facts = any(
                token in q for token in (
                    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verf\u00fcgbar"
                )
            )
            if asks_for_concrete_local_facts and not has_context:
                if rec:
                    rec.event("answer_postprocessed", "Anfrage ohne Kontext abgebrochen", status="skipped",
                              summary="Kein Kontext verf\u00fcgbar f\u00fcr konkrete Fragen")
                _append_room_ai_message(text=f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]")
                if rec:
                    rec.event("chat_message_written", "Hinweis in Raum geschrieben", status="completed")
                if store and trace_id:
                    store.complete_trace(trace_id)
                return

            llm_start = time.time()
            if rec:
                rec.event("llm_call_started", "LLM-Aufruf gestartet", status="running",
                          details={"provider": provider, "model": model})

            answer = generate_text(
                prompt=grounded_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
                timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
            )

            llm_ms = (time.time() - llm_start) * 1000
            if rec:
                rec.event("llm_call_completed", "LLM-Aufruf abgeschlossen", status="completed",
                          duration_ms=llm_ms,
                          output_preview=str(answer or "")[:500])

            text = str(answer or "").strip()
            asked_for_link = any(token in prompt.lower() for token in ("link", "url", "quelle", "source"))
            if text and not asked_for_link:
                text = text.replace("http://", "").replace("https://", "")
            if len(text) > 2200:
                text = text[:2200].rstrip() + "\n\n[gekuerzt]"
            if not text:
                text = "AI-Snake konnte gerade keine Antwort erzeugen."
            text = f"{text}\n\n[{context_summary}]"

            if rec:
                rec.event("answer_postprocessed", "Antwort aufbereitet", status="completed",
                          summary=f"{len(text)} Zeichen, Kontext angeh\u00e4ngt")

            _append_room_ai_message(text=text)

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
            _append_room_ai_message(text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.")

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()


def _auth_token(snake_id: str) -> str | None:
    """Extract Bearer token from Authorization header. Returns None if missing."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _verify_token(snake_id: str) -> bool:
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return False
    token = _auth_token(snake_id)
    return token is not None and secrets.compare_digest(str(snake.get("token") or ""), token)


def _pick_worker_for_ask() -> tuple[str, str | None]:
    """Return (worker_url, token) for the first online worker, or ("", None)."""
    try:
        from agent.services.agent_registry_service import get_agent_registry_service
        from agent.services.repository_registry import get_repository_registry

        agents = get_agent_registry_service().get_online_agents()
        if not agents:
            return "", None
        agent = agents[0]
        worker_url = str(getattr(agent, "url", "") or "").strip()
        if not worker_url:
            return "", None
        token: str | None = None
        try:
            db_agent = get_repository_registry().agent_repo.get_by_url(worker_url)
            token = str(getattr(db_agent, "token", "") or "").strip() or None
        except Exception:
            pass
        return worker_url, token
    except Exception:
        return "", None


def _resolve_lmstudio_model_for_worker(configured: str | None) -> str | None:
    """Resolve an actual LMStudio model ID, bypassing smoke/placeholder names."""
    try:
        from agent.llm_integration import _list_lmstudio_candidates, _select_best_lmstudio_model, _prepare_lmstudio_history
        from agent.config import settings as _s

        base_url = str(getattr(_s, "lmstudio_url", "") or "").rstrip("/")
        if not base_url:
            return configured
        candidates = _list_lmstudio_candidates(base_url, timeout=5)
        if not candidates:
            return configured
        if configured and "smoke" not in configured.lower() and "ananta" not in configured.lower():
            from agent.llm_integration import _find_matching_lmstudio_candidate
            matched = _find_matching_lmstudio_candidate(configured, candidates)
            if matched:
                return str(matched.get("id") or configured)
        history = _prepare_lmstudio_history(candidates)
        best = _select_best_lmstudio_model(candidates, history)
        return str((best or candidates[0]).get("id") or "")
    except Exception:
        return configured


def _worker_propose(
    grounded_prompt: str,
    model: str | None,
    *,
    limits: SnakeAskLimits | None = None,
    retrieval_profile_trace: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Forward prompt to worker /step/propose. Returns (answer, trace)."""
    from agent.services.task_runtime_service import forward_to_worker

    effective_limits = limits or SnakeAskLimits()
    trace: dict[str, Any] = {}
    worker_url, token = _pick_worker_for_ask()
    trace["worker_url"] = worker_url
    if not worker_url:
        trace["error"] = "no_online_worker"
        return "", trace

    resolved_model = _resolve_lmstudio_model_for_worker(model)
    trace["model_requested"] = model
    trace["model_resolved"] = resolved_model
    payload: dict[str, Any] = {
        "prompt": grounded_prompt,
        "provider": "lmstudio",
        "temperature": 0.3,
        "max_context_chars": effective_limits.context_chars,
    }
    if resolved_model:
        payload["model"] = resolved_model
    if effective_limits.max_tokens is not None:
        payload["max_tokens"] = effective_limits.max_tokens
    trace["prompt_chars"] = len(grounded_prompt)
    trace["prompt_preview"] = grounded_prompt[:300]
    trace["limits"] = {
        "context_chars": effective_limits.context_chars,
        "answer_chars": effective_limits.answer_chars,
        "max_tokens": effective_limits.max_tokens,
        "rag_top_k": effective_limits.rag_top_k,
    }
    if retrieval_profile_trace:
        analysis_mode = str(retrieval_profile_trace.get("analysis_mode") or "standard")
        trace["full_scan"] = {
            "status": "delegated_to_worker" if analysis_mode == "architecture_full_scan" else "not_requested",
            "analysis_mode": analysis_mode,
            "profile_id": retrieval_profile_trace.get("profile_id"),
            "output_intent": retrieval_profile_trace.get("output_intent"),
            "coverage_policy": retrieval_profile_trace.get("coverage_policy"),
            "plan_id": None,
            "artifact_paths": {},
        }

    try:
        result = forward_to_worker(worker_url, "/step/propose", payload, token=token)
        if result is None and token:
            result = forward_to_worker(worker_url, "/step/propose", payload, token=None)
    except Exception as exc:
        logging.getLogger(__name__).debug("snake-ask worker forward failed: %s", exc)
        trace["error"] = str(exc)[:120]
        return "", trace

    trace["worker_raw_response"] = str(result)[:500] if result else None
    if not isinstance(result, dict):
        trace["error"] = "non_dict_response"
        return "", trace
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        trace["error"] = "no_data_field"
        return "", trace
    text = str(data.get("reason") or data.get("raw") or data.get("answer") or "").strip()
    if len(text) > effective_limits.answer_chars:
        text = text[:effective_limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
    trace["answer_chars"] = len(text)
    return text, trace


# ── Route endpoints ────────────────────────────────────────────────────────────


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["POST"])
def chat_send(snake_id: str):
    """POST /snakes/<id>/chat/messages -- ChatMessage-v1 senden."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
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

    if not text:
        return jsonify({"error": "text erforderlich"}), 400

    if visibility == "local_only":
        return jsonify({"error": "local_only Nachrichten werden am Hub abgelehnt"}), 422

    if channel_type not in _VALID_CHANNEL_TYPES:
        return jsonify({"error": f"ung\u00fcltiger channel_type: {channel_type}"}), 422

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
    }

    if channel_type == "room":
        global _room_messages  # noqa: PLW0602
        existing_ids = {m["id"] for m in _room_messages}
        if msg["id"] not in existing_ids:
            _room_messages.append(msg)
            if len(_room_messages) > _MAX_ROOM_MSGS:
                _room_messages = _room_messages[-_MAX_ROOM_MSGS:]
            _spawn_ai_chat_reply(user_text=text, snake_id=snake_id)
    elif channel_type == "direct":
        target_ids = msg["target_ids"]
        if not target_ids:
            return jsonify({"error": "target_ids erforderlich f\u00fcr direct"}), 422
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
        return jsonify({"error": f"channel_type {channel_type} nicht unterst\u00fctzt"}), 422

    return jsonify({"ok": True, "id": msg["id"]}), 202


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["GET"])
def chat_receive(snake_id: str):
    """GET /snakes/<id>/chat/messages?since=<cursor> -- Chat-Nachrichten abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404

    since_str = request.args.get("since", "")
    since: float = float(since_str) if since_str else 0.0

    direct = [m for m in _chat_messages.get(snake_id, []) if float(m.get("created_at") or 0) > since]
    room = [m for m in _room_messages if float(m.get("created_at") or 0) > since and m.get("sender_id") != snake_id]

    all_msgs = sorted(direct + room, key=lambda m: float(m.get("created_at") or 0))

    if direct:
        delivered_ids = {m["id"] for m in direct}
        _chat_messages[snake_id] = [m for m in _chat_messages.get(snake_id, []) if m["id"] not in delivered_ids]

    new_cursor = str(time.time()) if all_msgs else since_str

    return jsonify({"messages": all_msgs, "cursor": new_cursor}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/cancel", methods=["POST"])
def chat_cancel(snake_id: str):
    """POST /snakes/<id>/chat/cancel -- Laufenden full_scan abbrechen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    cancelled = False
    for key in ("room", "snake_ask", snake_id):
        event = _SCAN_CANCELS.get(key)
        if event:
            event.set()
            cancelled = True
    return jsonify({"ok": True, "cancelled": cancelled}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/ack", methods=["POST"])
def chat_ack(snake_id: str):
    """POST /snakes/<id>/chat/ack -- Gelesene Nachrichten best\u00e4tigen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


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
    """POST /snake/ask -- Synchrone AI-Antwort f\u00fcr den TUI ananta-worker Modus.

    Akzeptiert v1 ({question, context, depth}) und v2 ({question, context, depth, memory_context}).
    Optionales Feld "debug": true gibt trace-Infos zur\u00fcck.
    Antwortet mit {"answer": "..."}. Routet \u00fcber einen registrierten Worker-Prozess;
    f\u00e4llt auf direkten LMStudio-Aufruf zur\u00fcck falls kein Worker verf\u00fcgbar.
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
        grounded_prompt, has_context, context_summary, domain_scope_info = _build_grounded_snake_prompt(
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
    }

    provider, hub_model = _resolve_ai_snake_chat_provider()
    model = request_model or hub_model

    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import _is_full_scan_intent

        _eff_cfg = _current_config()
        _eff_cfg.update(dict(retrieval_config_overrides or {}))
        if _is_full_scan_intent(question, "", _eff_cfg):
            answer, worker_trace = _worker_chat_full_scan(question, provider=provider, model=model, limits=limits, cancel_key="snake_ask")
            if answer:
                files_found = worker_trace.get("files_found", 0)
                batches_done = worker_trace.get("batches_completed", 0)
                summary = f"full_scan: {batches_done} Batches, {files_found} Quelldateien"
                if len(answer) > limits.answer_chars:
                    answer = answer[:limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
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
        limits=limits,
        retrieval_profile_trace=rag_trace.get("retrieval_profile") if isinstance(rag_trace.get("retrieval_profile"), dict) else None,
    )
    if answer:
        resp = {"answer": answer, "path": "worker", **domain_scope_info}
        if debug:
            resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
        elif retrieval_config_overrides and isinstance(rag_trace.get("retrieval_profile"), dict):
            resp["trace"] = {"rag": rag_trace}
        return jsonify(resp), 200

    try:
        provider, _ = _resolve_ai_snake_chat_provider()
        timeout = min(int(getattr(settings, "http_timeout", 120) or 120), 180)
        raw = generate_text(
            prompt=grounded_prompt,
            provider=provider,
            model=model,
            history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
            max_output_tokens=limits.max_tokens,
            timeout=timeout,
        )
        text = str(raw or "").strip()
        if len(text) > limits.answer_chars:
            text = text[:limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
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
