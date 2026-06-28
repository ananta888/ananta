"""Chat helper functions for snake — answer limits, room messages, grounded prompt, UI guide."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.rag_service import get_rag_service

from .snakes_retrieval_helpers import (
    _build_local_repo_fallback_context,
    _domain_scope_response,
    _resolve_domain_scope_for_chat,
)


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
    from agent.routes.snakes import _room_messages, _MAX_ROOM_MSGS  # lazy to avoid circular import
    _room_messages.append(msg)
    if len(_room_messages) > _MAX_ROOM_MSGS:
        del _room_messages[:-_MAX_ROOM_MSGS]


def _build_room_conversation_history(
    *,
    snake_id: str | None,
    current_text: str,
    session_id: str = "",
    max_messages: int = 8,
) -> list[dict[str, str]]:
    """Return recent room messages before the current user turn for LLM history."""
    from agent.routes.snakes import _room_messages  # lazy to avoid circular import
    current = str(current_text or "").strip()
    requested_session_id = str(session_id or "").strip()
    current_idx: int | None = None
    for idx in range(len(_room_messages) - 1, -1, -1):
        msg = _room_messages[idx]
        if requested_session_id and str(msg.get("session_id") or "") != requested_session_id:
            continue
        if (
            str(msg.get("sender_id") or "") == str(snake_id or "")
            and str(msg.get("sender_kind") or "") == "user"
            and str(msg.get("text") or "").strip() == current
        ):
            current_idx = idx
            break

    prior_messages = _room_messages[:current_idx] if current_idx is not None else list(_room_messages)
    if requested_session_id:
        prior_messages = [
            msg for msg in prior_messages
            if str(msg.get("session_id") or "") == requested_session_id
        ]
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

        # QIE-001: Phase 0 — extract clean search intent before CodeCompass
        from agent.services.query_intent_extractor import extract_query_intent
        _qi = extract_query_intent(prompt, cfg)
        _search_query = _qi.search_query

        bundle, grounded = get_rag_service().build_execution_context(
            _search_query,
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


def _should_include_light_ui_context(
    *,
    active_session_id: str,
    active_session_group: str = "",
    active_session_settings: dict[str, Any] | None = None,
) -> bool:
    """Whether a normal chat session should receive the lightweight UI hint."""
    sid = str(active_session_id or "").strip()
    if not sid or sid in {"ananta-settings", "ananta-visual"}:
        return False
    settings = dict(active_session_settings or {})
    if settings.get("chat_include_ui_context") is False:
        return False
    if str(active_session_group or "").strip().lower() == "architektur":
        return False
    return True
