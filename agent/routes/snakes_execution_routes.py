"""Snake execution endpoints — chat API, ask, worker-context."""

from __future__ import annotations

import logging
import os
import secrets
import sys
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
from agent.services.snake_chat_cancellation import (
    cancel_chat,
    register_chat_cancel,
    unregister_chat_cancel,
)

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

_SNAKE_RETRIEVAL_CONFIG_KEYS = frozenset({
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_codecompass_trigger_mode",
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_include_task_memory",
    "chat_source_pack_id",
})


@dataclass(frozen=True, slots=True)
class SnakeAskLimits:
    context_chars: int = 4000
    answer_chars: int = 2200
    max_tokens: int | None = None
    rag_top_k: int | None = None
    answer_overflow_policy: str = "allow"
    never_truncate_answers: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SnakeAskLimits":
        return cls(
            context_chars=_bounded_optional_int(payload.get("context_chars"), default=4000, minimum=500, maximum=20000),
            answer_chars=_bounded_optional_int(payload.get("answer_chars"), default=2200, minimum=600, maximum=50000),
            max_tokens=_bounded_optional_int(payload.get("max_tokens"), default=None, minimum=100, maximum=8000),
            rag_top_k=_bounded_optional_int(payload.get("rag_top_k"), default=None, minimum=1, maximum=120),
            answer_overflow_policy=_answer_overflow_policy(payload.get("answer_overflow_policy")),
            never_truncate_answers=_optional_bool(payload.get("never_truncate_answers"), default=True),
        )


def _optional_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on", "an", "ja"}:
        return True
    if token in {"0", "false", "no", "off", "aus", "nein"}:
        return False
    return default


def _bounded_optional_int(value: Any, *, default: int | None, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _answer_overflow_policy(value: Any | None = None) -> str:
    raw = value
    if raw is None or raw == "":
        try:
            from agent.routes.ai_snake_config import _current_config

            raw = _current_config().get("chat_answer_overflow_policy")
        except Exception:
            raw = None
    policy = str(raw or "allow").strip().lower()
    return policy if policy in {"allow", "summarize", "truncate"} else "allow"


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
) -> tuple[str, bool, str, dict[str, Any], list[dict[str, Any]]]:
    """Returns (grounded_prompt, has_context, summary, domain_info, chunk_meta).

    chunk_meta is a list of dicts with keys: path, source_type, score.
    """
    prompt = str(user_text or "").strip()
    if not prompt:
        return prompt, False, "", {}, []
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
            chunk_meta: list[dict[str, Any]] = []
            for chunk in chunks:
                metadata = dict((chunk or {}).get("metadata") or {})
                st = str(metadata.get("source_type") or (chunk or {}).get("engine") or "unknown").strip().lower() or "unknown"
                src_type_counts[st] = int(src_type_counts.get(st, 0)) + 1
                path = str(
                    metadata.get("file_path") or metadata.get("path")
                    or metadata.get("source_id") or (chunk or {}).get("source")
                    or (chunk or {}).get("path") or ""
                ).strip()
                if path.startswith("/app/"):
                    path = path[5:]
                score = float((chunk or {}).get("score") or metadata.get("score") or 0.0)
                if path and len(chunk_meta) < 40:
                    chunk_meta.append({"path": path, "source_type": st, "score": round(score, 3)})
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
            return grounded, True, summary, domain_scope_info, chunk_meta
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
        return prompt, True, "Kontext: 1 Treffer (repo_fallback:1)", {}, []
    return prompt, False, "Kontext: 0 Treffer", {}, []


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
                max_chunks=max(8, min(top_k if top_k is not None else 40, 40)),
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


def _chat_answer_chars_limit(default: int = 12000) -> int:
    try:
        from agent.routes.ai_snake_config import _current_config

        return max(600, min(50000, int(_current_config().get("chat_answer_chars") or default)))
    except Exception:
        return default


def _chat_never_truncate_answers(default: bool = True) -> bool:
    try:
        from agent.routes.ai_snake_config import _current_config

        return _optional_bool(_current_config().get("chat_never_truncate_answers"), default=default)
    except Exception:
        return default


def _answer_budget_instruction(limit: int, *, policy: str | None = None) -> str:
    resolved_policy = _answer_overflow_policy(policy)
    if resolved_policy == "allow":
        return ""
    action = "fasse aktiv zusammen" if resolved_policy == "summarize" else "halte die Antwort strikt kurz"
    return (
        f"Antwort-Budget: maximal {max(600, min(50000, int(limit or 0)))} Zeichen. "
        f"Wenn die vollstaendige Antwort laenger waere, {action} statt mitten im Satz abzubrechen."
    )


def _with_answer_budget_instruction(prompt: str, limit: int, *, policy: str | None = None) -> str:
    instruction = _answer_budget_instruction(limit, policy=policy)
    if not instruction:
        return prompt
    return f"{prompt}\n\n{instruction}"


def _fit_answer_to_chars(
    text: str,
    *,
    limit: int,
    provider: str,
    model: str | None,
    timeout: int = 60,
    overflow_policy: str | None = None,
    never_truncate: bool | None = None,
) -> str:
    value = str(text or "").strip()
    safe_limit = max(600, min(50000, int(limit or 0)))
    if len(value) <= safe_limit:
        return value
    policy = _answer_overflow_policy(overflow_policy)
    if policy == "allow":
        return value
    if policy == "truncate":
        marker = "\n\n[gekuerzt]"
        return value[: max(0, safe_limit - len(marker))].rstrip() + marker

    compress_prompt = (
        "Verdichte die folgende Antwort, ohne neue Fakten zu erfinden.\n"
        f"Ziel: maximal {safe_limit} Zeichen.\n"
        "Bewahre die wichtigen konkreten Aussagen, Dateinamen, Begriffe und Entscheidungen.\n"
        "Antworte auf Deutsch und gib nur die verdichtete Antwort aus.\n\n"
        "Antwort:\n"
        f"{value}"
    )
    try:
        max_output_tokens = max(200, min(8000, safe_limit // 3))
        compressed = generate_text(
            prompt=compress_prompt,
            provider=provider,
            model=model,
            max_output_tokens=max_output_tokens,
            timeout=max(10, min(int(timeout or 60), 120)),
        )
        compressed_text = str(compressed or "").strip()
        if compressed_text and len(compressed_text) <= safe_limit:
            return compressed_text
        if compressed_text:
            value = compressed_text
    except Exception:
        pass

    if (never_truncate if never_truncate is not None else _chat_never_truncate_answers()):
        return value

    marker = "\n\n[gekuerzt]"
    return value[: max(0, safe_limit - len(marker))].rstrip() + marker


def _append_room_ai_message(*, text: str, session_id: str = "", visibility: str = "room",
                            sender_id: str = "ai-snake", ui_snapshot: str = "") -> None:
    if not text:
        return
    msg: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "created_at": time.time(),
        "channel_id": "room:main",
        "channel_type": "room",
        "sender_id": sender_id,
        "sender_kind": "assistant" if sender_id == "ai-snake" else "system",
        "target_ids": [],
        "text": text,
        "visibility": visibility,
        "delivery_state": "received",
        "policy_decision_ref": None,
        "session_id": session_id,
    }
    if ui_snapshot:
        msg["ui_snapshot"] = ui_snapshot[:500]
    global _room_messages
    _room_messages.append(msg)
    if len(_room_messages) > _MAX_ROOM_MSGS:
        _room_messages = _room_messages[-_MAX_ROOM_MSGS:]


# ── Visual snake session (ananta-visual) ───────────────────────────────────────
_visual_last_snapshot: str = ""
_visual_last_reply_at: float = 0.0
_VISUAL_THROTTLE_S: float = 25.0  # minimum seconds between visual replies
_VISUAL_SESSION_ID: str = "ananta-visual"  # tag for messages belonging to the visual snake session


def _visual_session_log_deltas_only() -> bool:
    """Read the predictive_guide_log_deltas_only flag from the active
    ananta-visual session settings, defaulting to True if the session
    can't be found or has no settings yet.

    Reads the raw chat_sessions list from the manager directly so we get
    the user's persisted value — going through get_sessions() would
    re-add the built-in default 'ananta-visual' (with log_deltas_only
    absent → True default) when the user has wiped the list.

    Imports get_manager at module top (not inside the try) so the
    conftest's monkeypatch on user_config_manager.get_manager takes
    effect — same pattern used by _load_chat / _save_chat in chat.py."""
    from client_surfaces.operator_tui.config.user_config_manager import get_manager
    try:
        sessions = get_manager().load().get("chat_sessions") or []
        sess = next(
            (s for s in sessions if str(s.get("id") or "") == _VISUAL_SESSION_ID),
            None,
        )
        if not sess:
            return True
        return bool((sess.get("settings") or {}).get("predictive_guide_log_deltas_only", True))
    except Exception:
        return True


def _append_visual_user_tick(*, ui_snapshot: str) -> None:
    """Persist the incoming UI snapshot as a system message in the ananta-visual session
    so the user can later review what the visual snake observed.

    When the session has predictive_guide_log_deltas_only=True, also append
    a [ui-delta] system message containing the human-readable diff between
    the previous and current snapshot."""
    global _visual_last_snapshot
    text = f"[ui-tick] {ui_snapshot}" if ui_snapshot else "[ui-tick] (leer)"
    _append_room_ai_message(
        text=text,
        session_id=_VISUAL_SESSION_ID,
        visibility="system",
        sender_id="browser",
        ui_snapshot=ui_snapshot,
    )
    # ── Delta log (optional, opt-in via session setting) ──────────────────
    if not ui_snapshot:
        return
    log_deltas = _visual_session_log_deltas_only()
    if log_deltas:
        try:
            from agent.services.snapshot_delta import diff_snapshots
            delta = diff_snapshots(_visual_last_snapshot or "", ui_snapshot)
            if not delta.is_empty():
                delta_text = f"[ui-delta] {delta.as_compact_text()}"
                _append_room_ai_message(
                    text=delta_text,
                    session_id=_VISUAL_SESSION_ID,
                    visibility="system",
                    sender_id="browser",
                )
        except Exception as exc:  # never let the delta path break the raw tick
            logging.getLogger(__name__).debug("ananta-visual delta log failed: %s", exc)
    # Track the most recent snapshot for next call
    _visual_last_snapshot = ui_snapshot


def _spawn_visual_reply(ui_snapshot: str) -> None:
    """Background: generate a short proactive guide response for the visual snake session.
    Only fires when the UI snapshot has meaningfully changed and enough time has passed."""
    global _visual_last_snapshot, _visual_last_reply_at
    now = time.time()
    if ui_snapshot == _visual_last_snapshot:
        return
    if now - _visual_last_reply_at < _VISUAL_THROTTLE_S:
        return
    _visual_last_snapshot = ui_snapshot
    _visual_last_reply_at = now

    _log = logging.getLogger(__name__)
    _log.info("ananta-visual: generating proactive guide for snapshot=%r", ui_snapshot[:80])

    try:
        from agent.routes.ai_snake_config import _current_config
        _cfg = _current_config()
        provider = str(_cfg.get("chat_provider") or "openai")
        model    = str(_cfg.get("chat_model") or "gpt-4o-mini")
        api_base = str(_cfg.get("chat_api_base") or "")
        api_key  = str(_cfg.get("chat_api_key")  or "")

        system_prompt = (
            "Du bist die orangene Guide-Snake in der Ananta App — eine kleine KI-Schlange "
            "die den User visuell durch die App führt.\n"
            "Du bekommst den aktuellen UI-Zustand als kompakten Text.\n"
            "Reagiere in 1-2 kurzen deutschen Sätzen auf das was der User gerade sieht.\n"
            "Füge wenn sinnvoll __GUIDE__: Steps an (JSON, Format: {\"steps\":[{\"waypoint\":\"...\",\"bubble\":\"...\",\"delay_ms\":3000}]}).\n"
            "Wenn der Zustand trivial/unklar ist, antworte mit leerem Text."
        )
        user_msg = f"Aktueller UI-Zustand des Users:\n{ui_snapshot}"

        import openai as _oai
        _client_kwargs: dict[str, Any] = {"api_key": api_key or "sk-no-key"}
        if api_base:
            _client_kwargs["base_url"] = api_base
        _client = _oai.OpenAI(**_client_kwargs)
        _resp = _client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_msg},
            ],
            max_tokens=200,
            temperature=0.4,
        )
        answer = (_resp.choices[0].message.content or "").strip()
        if answer:
            _append_room_ai_message(
                text=answer,
                session_id=_VISUAL_SESSION_ID,
                visibility="room",
            )
            _log.info("ananta-visual: reply appended (%d chars)", len(answer))
    except Exception as exc:
        logging.getLogger(__name__).warning("ananta-visual reply failed: %s", exc)


def _build_room_conversation_history(
    *,
    snake_id: str | None,
    current_text: str,
    max_messages: int = 8,
) -> list[dict[str, str]]:
    """Return recent room messages before the current user turn for LLM history."""
    current = str(current_text or "").strip()
    current_idx: int | None = None
    for idx in range(len(_room_messages) - 1, -1, -1):
        msg = _room_messages[idx]
        if (
            str(msg.get("sender_id") or "") == str(snake_id or "")
            and str(msg.get("sender_kind") or "") == "user"
            and str(msg.get("text") or "").strip() == current
        ):
            current_idx = idx
            break

    prior_messages = _room_messages[:current_idx] if current_idx is not None else list(_room_messages)
    history: list[dict[str, str]] = []
    for msg in prior_messages[-max(1, int(max_messages)) :]:
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        sender_id = str(msg.get("sender_id") or "")
        sender_kind = str(msg.get("sender_kind") or "")
        role = "assistant" if sender_kind == "assistant" or sender_id == "ai-snake" else "user"
        history.append({"role": role, "content": text[:2000]})
    return history


from .snakes_full_scan import _SCAN_CANCELS as _FULL_SCAN_CANCELS
from .snakes_full_scan import worker_chat_full_scan as _worker_chat_full_scan
from .snakes_rag_iterative import worker_chat_rag_iterative as _worker_chat_rag_iterative


def _trace_feature_enabled() -> bool:
    try:
        from agent.routes.ai_snake_config import _current_config
        cfg = _current_config()
        return bool(cfg.get("ai_snake_trace_enabled", True))
    except Exception:
        return True


# ── Ananta-Settings guided tour ───────────────────────────────────────────────

def _ensure_ui_guide(force: bool = False) -> str:
    """Return the UI guide markdown, generating/refreshing as needed."""
    try:
        from agent.routes.snakes_ananta_config_tool_loop import ensure_ui_guide
        return ensure_ui_guide(force=force)
    except Exception as exc:
        logging.getLogger(__name__).warning("UI guide unavailable: %s", exc)
        return ""


def _read_ananta_settings_summary() -> str:
    """Return current live settings + the UI guide (generated on demand)."""
    parts: list[str] = []
    try:
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        s = get_manager().load()
        active_sid = str(s.get("chat_active_session_id") or "")
        sessions = s.get("chat_sessions") or []
        active_sess = next((x for x in sessions if str(x.get("id") or "") == active_sid), None)
        sess_cfg = (active_sess or {}).get("settings") or {}
        backend = str(sess_cfg.get("chat_backend") or s.get("chat_backend") or "unbekannt")
        model = str(sess_cfg.get("chat_backend_model") or s.get("chat_backend_model") or "unbekannt")
        cc_on = bool(sess_cfg.get("chat_use_codecompass", s.get("chat_use_codecompass")))
        profile = sess_cfg.get("chat_retrieval_profile") or s.get("chat_retrieval_profile") or "auto"

        sess_lines = []
        for sx in sessions:
            sid = str(sx.get("id") or "")
            sname = str(sx.get("name") or sid)
            scfg = sx.get("settings") or {}
            sb = str(scfg.get("chat_backend") or "")
            sm = str(scfg.get("chat_backend_model") or "")
            sess_lines.append(f"  - {sname} ({sid}): backend={sb or '(global)'} model={sm or '(global)'}")

        parts.append("\n".join([
            "## Aktuelle Ananta-Einstellungen (live)",
            f"- Aktive Session: {active_sid or '(keine)'}",
            f"- Standard-Backend: {backend}",
            f"- Standard-Modell: {model}",
            f"- CodeCompass: {'an' if cc_on else 'aus'}",
            f"- Retrieval-Profil: {profile}",
            f"- Konfigurierte Chat-Sessions ({len(sessions)}):",
            *sess_lines,
        ]))
    except Exception as exc:
        parts.append(f"(Live-Einstellungen nicht lesbar: {exc})")

    guide = _ensure_ui_guide()
    if guide:
        parts.append(guide)

    return "\n\n".join(parts)


_ANANTA_UI_GUIDE_MAP: list[tuple[list[str], list[dict]]] = [
    (
        ["pair", "pair dev", "pair-dev", "pairdev", "pari", "pari-dev", "pairing", "share session", "share-session", "zusammen", "kollaboration"],
        [
            {"waypoint": "assistant.snake-chat-btn", "bubble": "'Snake Chat' öffnen (💬 unten rechts)", "delay_ms": 3000},
            {"waypoint": "assistant.tab-pair-dev", "bubble": "Tab 'Pair Dev' wählen", "delay_ms": 3000},
            {"waypoint": "snake.tab-pair", "bubble": "Hier Pair-Dev-Session starten oder beitreten", "delay_ms": 4000},
        ],
    ),
    (
        ["chat session", "neue session", "new session", "konversation anlegen", "chat anlegen"],
        [
            {"waypoint": "nav./chats", "bubble": "Zum Bereich 'AI Chats' navigieren", "delay_ms": 2500},
            {"waypoint": "chat.new-session", "bubble": "Mit '+' neue Chat-Session anlegen", "delay_ms": 3000},
            {"waypoint": "chat.settings-tab", "bubble": "Tab 'Einstellungen' öffnen", "delay_ms": 3000},
            {"waypoint": "chat.backend-select", "bubble": "Hier Backend auswählen (z.B. ananta-worker)", "delay_ms": 3500},
            {"waypoint": "chat.system-prompt", "bubble": "System-Prompt für diese Session eingeben", "delay_ms": 4000},
        ],
    ),
    (
        ["modell", "model", "provider", "llm", "openai", "lmstudio", "hermes", "backend wechseln", "backend ändern"],
        [
            {"waypoint": "nav./chats", "bubble": "Zum Chat-Bereich navigieren", "delay_ms": 2000},
            {"waypoint": "chat.settings-tab", "bubble": "Einstellungen der aktiven Session öffnen", "delay_ms": 3000},
            {"waypoint": "chat.backend-select", "bubble": "Hier Backend/Modell für die Session wechseln", "delay_ms": 4000},
        ],
    ),
    (
        ["worker", "agent", "worker pool", "workerpool"],
        [
            {"waypoint": "cc.workers", "bubble": "Control Center → Workers öffnen", "delay_ms": 3500},
        ],
    ),
    (
        ["blueprint erstell", "blueprint anleg", "neues blueprint", "blueprint creat", "blueprint bau"],
        [
            {"waypoint": "nav./teams", "bubble": "Navigiere zu 'Teams & Blueprints' im Menü", "delay_ms": 3000},
            {"waypoint": "teams.tab-blueprints", "bubble": "Tab 'Blueprints' öffnen", "delay_ms": 2500},
            {"waypoint": "teams.blueprint-catalog", "bubble": "Hier siehst du den Blueprint-Katalog — wähle einen aus oder erstelle einen neuen", "delay_ms": 4000},
        ],
    ),
    (
        ["blueprint", "vorlage"],
        [
            {"waypoint": "nav./teams", "bubble": "Blueprints findest du unter 'Teams & Blueprints'", "delay_ms": 3000},
            {"waypoint": "teams.tab-blueprints", "bubble": "Tab 'Blueprints' öffnen", "delay_ms": 3000},
        ],
    ),
    (
        ["policy", "richtlinie", "approval", "genehmigung", "freigabe"],
        [
            {"waypoint": "cc.policies", "bubble": "Control Center → Policy-Genehmigungen öffnen", "delay_ms": 3000},
        ],
    ),
    (
        ["codecompass", "rag", "retrieval", "code compass"],
        [
            {"waypoint": "cc.codecompass", "bubble": "Control Center → CodeCompass-Verwaltung öffnen", "delay_ms": 3000},
            {"waypoint": "chat.retrieval-profile", "bubble": "Retrieval-Profil in Session-Einstellungen", "delay_ms": 3500},
        ],
    ),
    (
        ["einstellungen", "settings", "konfigurieren", "konfiguration", "einrichten", "setup"],
        [
            {"waypoint": "assistant.snake-chat-btn", "bubble": "'Snake Chat' öffnen (💬 unten rechts)", "delay_ms": 2500},
            {"waypoint": "assistant.tab-settings", "bubble": "Tab 'Einstellungen' öffnen", "delay_ms": 3000},
            {"waypoint": "snake.tab-settings", "bubble": "Hier Snake-Chat-Einstellungen anpassen", "delay_ms": 3500},
        ],
    ),
]


def _build_ui_guide(prompt: str) -> dict | None:
    """Return a guide dict for the UI if the prompt matches a known topic."""
    q = str(prompt or "").lower()
    for keywords, steps in _ANANTA_UI_GUIDE_MAP:
        if any(kw in q for kw in keywords):
            return {"steps": steps}
    return None


def _spawn_ai_chat_reply(*, user_text: str, snake_id: str | None = None, ui_context: dict | None = None, client_session_id: str = "") -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        nonlocal prompt
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
                _prompt_preview = prompt[:120] + ("\u2026" if len(prompt) > 120 else "")
                rec.event(
                    "request_received", "Anfrage empfangen",
                    status="completed",
                    summary=f"Prompt: {_prompt_preview}",
                )

            provider, model, api_base = _resolve_ai_snake_chat_provider()
            conversation_history = _build_room_conversation_history(snake_id=snake_id, current_text=prompt)
            if rec:
                rec.event("config_loaded", "Provider-Konfiguration geladen", status="completed",
                          details={"provider": provider, "model": model, "conversation_history_messages": len(conversation_history)})

            # Resolve active session's system_prompt, ID, and settings overrides
            _active_session_prompt: str | None = None
            _active_session_id: str = ""
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
                rec.event("retrieval_profile_selected", "Retrieval-Profil wird aufgel\u00f6st", status="running",
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
                    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verf\u00fcgbar"
                )
            )
                        # Skip the "no-context" short-circuit for ananta-settings (it intentionally has no RAG)
            if asks_for_concrete_local_facts and not has_context and _active_session_id != "ananta-settings":
                if rec:
                    rec.event("answer_postprocessed", "Anfrage ohne Kontext abgebrochen", status="skipped",
                              summary="Kein Kontext verf\u00fcgbar f\u00fcr konkrete Fragen")
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
                          summary=f"{len(text)} Zeichen, Kontext angeh\u00e4ngt")

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
    provider: str = "lmstudio",
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
        "provider": provider,
        "temperature": 0.3,
        "max_context_chars": effective_limits.context_chars,
        "answer_chars": effective_limits.answer_chars,
        "answer_overflow_policy": effective_limits.answer_overflow_policy,
        "never_truncate_answers": effective_limits.never_truncate_answers,
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
        "answer_overflow_policy": effective_limits.answer_overflow_policy,
        "never_truncate_answers": effective_limits.never_truncate_answers,
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
    text = _fit_answer_to_chars(
        text,
        limit=effective_limits.answer_chars,
        provider=provider,
        model=resolved_model,
        timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
        overflow_policy=effective_limits.answer_overflow_policy,
        never_truncate=effective_limits.never_truncate_answers,
    )
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
            _append_visual_user_tick(ui_snapshot=_ui_snap)
            import threading as _thr
            _thr.Thread(target=_spawn_visual_reply, args=(_ui_snap,), daemon=True).start()
        return jsonify({"ok": True, "id": str(body.get("id") or "")}), 202

    if channel_type not in _VALID_CHANNEL_TYPES:
        return jsonify({"error": f"ung\u00fcltiger channel_type: {channel_type}"}), 422

    # Backend-side guard: the ananta-visual session is a read-only log. Only the
    # backend's [ui-tick] / proactive guide paths are allowed to write to it.
    if client_session_id == "ananta-visual" and not (visibility == "system" and text.startswith("[ui-tick]")):
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
    """POST /snakes/<id>/chat/cancel -- Laufenden AI-Snake-Chat abbrechen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
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
    """POST /snakes/<id>/chat/ack -- Gelesene Nachrichten best\u00e4tigen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


@snakes_bp.route("/snakes/<snake_id>/ui-state", methods=["PUT"])
def snake_ui_state_push(snake_id: str):
    """PUT /snakes/<id>/ui-state -- aktuellen UI-Zustand des Browsers speichern."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ung\u00fcltiger Token"}), 401
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
