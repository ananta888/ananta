"""T03.01: Snake-Registrierung und Chat-API über Hub.

Endpunkte:
  POST   /snakes                          – Snake registrieren
  GET    /snakes                          – alle aktiven Snakes auflisten
  DELETE /snakes/<id>                     – Snake abmelden
  POST   /snakes/<id>/messages            – Legacy: einfache Nachricht an Snake
  GET    /snakes/<id>/messages            – Legacy: Nachrichten abrufen
  POST   /snakes/<id>/heartbeat          – Liveness-Ping
  POST   /snakes/<id>/chat/messages      – ChatMessage-v1 senden
  GET    /snakes/<id>/chat/messages      – Chat-Nachrichten abrufen (cursor)
  POST   /snakes/<id>/chat/ack           – Gelesene Nachrichten bestätigen
  GET    /snakes/participants            – Teilnehmerliste mit Status
  POST   /snake/ask                      – Synchrone AI-Antwort (TUI worker mode)
  POST   /worker-context                 – WorkerContextHandoffV3 mit CandidateFiles (CWFH-009)
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import jwt
from flask import Blueprint, current_app, has_app_context, jsonify, request

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.rag_service import get_rag_service

snakes_bp = Blueprint("snakes", __name__)

# In-Memory-Store (pro Hub-Prozess); max 8 gleichzeitig aktive Snakes.
_MAX_SNAKES = 8
_snakes: dict[str, dict[str, Any]] = {}
_messages: dict[str, list[dict[str, Any]]] = {}

# Chat store: keyed by snake_id, each value is list of ChatMessage-v1 dicts
_chat_messages: dict[str, list[dict[str, Any]]] = {}
# Room-wide chat (broadcast)
_room_messages: list[dict[str, Any]] = []
_MAX_CHAT_MSGS = 500
_MAX_ROOM_MSGS = 200

_VALID_CHANNEL_TYPES = {"room", "direct", "ai", "system"}
_VALID_VISIBILITY = {"room", "direct", "ai_context", "system"}  # local_only is REJECTED

_VALID_ROLES = {"player", "tutor", "critic", "coach", "viewer"}
_VALID_COLORS = {"mint", "amber", "rose", "violet", "sky", "coral", "lime", "ice", "cyan"}


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
        from agent.routes.ai_snake_config import _current_config  # local import avoids route init coupling

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


_SNAKE_RETRIEVAL_CONFIG_KEYS = frozenset({
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
})


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


def _build_grounded_snake_prompt(
    user_text: str,
    *,
    limits: SnakeAskLimits | None = None,
    retrieval_config_overrides: dict[str, Any] | None = None,
) -> tuple[str, bool, str]:
    prompt = str(user_text or "").strip()
    if not prompt:
        return prompt
    effective_limits = limits or SnakeAskLimits()
    try:
        from agent.routes.ai_snake_config import _current_config  # local import avoids route init coupling
        from agent.services.retrieval_profile_service import resolve_profile  # CRPS-005

        cfg = _current_config()
        cfg.update(dict(retrieval_config_overrides or {}))

        # CRPS-005: resolve domain-aware retrieval profile instead of hard-coding source_types
        # chat_code_questions_repo_first is a shortcut to "repo_first" mode
        feature_flag = str(cfg.get("chat_retrieval_profile") or "auto").strip().lower()
        if bool(cfg.get("chat_code_questions_repo_first")) and feature_flag == "auto":
            feature_flag = "repo_first"
        domain_hint = str(cfg.get("chat_retrieval_domain_hint") or "").strip() or None

        profile = resolve_profile(prompt, cfg, domain_hint=domain_hint, feature_flag=feature_flag)

        bundle, grounded = get_rag_service().build_execution_context(
            prompt,
            task_kind="research",
            retrieval_intent=profile.retrieval_intent or "chat_codecompass_overview",
            source_types=profile.source_types or None,
            max_chunks=effective_limits.rag_top_k,
            retrieval_profile=profile.as_dict(),
        )
        chunks = list(bundle.get("chunks") or [])
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
            return grounded, True, summary
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
        return grounded, True, "Kontext: 1 Treffer (repo_fallback:1)"
    return prompt, False, "Kontext: 0 Treffer"


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
        return {
            "profile_id": profile.profile_id,
            "domain": profile.domain,
            "intent": profile.intent,
            "analysis_mode": profile.analysis_mode or "standard",
            "output_intent": profile.output_intent,
            "coverage_policy": profile.coverage_policy,
            "summary_policy": profile.summary_policy,
            "source_types": list(profile.source_types),
            "warnings": list(profile.warnings),
        }
    except Exception as exc:
        return {"error": str(exc)[:120]}


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


def _worker_chat_full_scan(
    question: str,
    *,
    provider: str = "lmstudio",
    model: str | None = None,
    limits: "SnakeAskLimits | None" = None,
) -> tuple[str, dict[str, Any]]:
    """Multi-batch source-code-only analysis for chat full_scan mode.

    Reads Python source files from the repo root in batches, sends each
    batch to the LLM for analysis, then synthesises the results into one answer.
    """
    import pathlib as _pl
    from agent.common.sgpt import _resolve_repo_root
    from agent.routes.ai_snake_config import _current_config

    effective_limits = limits or SnakeAskLimits()
    cfg = _current_config()
    trace: dict[str, Any] = {"mode": "full_scan_chat"}

    try:
        max_batches = max(1, min(16, int(float(cfg.get("chat_full_scan_max_batches") or 8))))
        files_per_batch = max(1, min(10, int(float(cfg.get("chat_full_scan_files_per_batch") or 3))))
        parallel_batches = max(1, min(8, int(float(cfg.get("chat_full_scan_parallel_batches") or 4))))
        source_only_val = cfg.get("chat_full_scan_source_only")
        source_only = source_only_val if isinstance(source_only_val, bool) else True
    except (TypeError, ValueError):
        max_batches, files_per_batch, parallel_batches, source_only = 8, 3, 4, True

    repo_root = _resolve_repo_root()
    if not repo_root:
        trace["error"] = "no_repo_root"
        return "", trace

    _SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
                  ".tox", "dist", "build", ".eggs", "project-workspaces", "tests", "test"}
    exts = (".py",) if source_only else (".py", ".ts")

    all_files: list[_pl.Path] = []
    for ext in exts:
        for f in repo_root.rglob(f"*{ext}"):
            if not any(part in _SKIP_DIRS for part in f.parts):
                all_files.append(f)
    all_files.sort(key=lambda f: str(f.relative_to(repo_root)))

    trace["files_found"] = len(all_files)
    if not all_files:
        trace["error"] = "no_source_files"
        return "", trace

    max_files = max_batches * files_per_batch
    selected = all_files[:max_files]
    batches = [selected[i:i + files_per_batch] for i in range(0, len(selected), files_per_batch)]
    trace["batches_planned"] = len(batches)
    trace["files_selected"] = len(selected)

    import concurrent.futures as _cf

    _PER_FILE_CHARS = 3500
    timeout_s = min(int(getattr(settings, "http_timeout", 120) or 120), 180)

    def _run_batch(args: tuple[int, list]) -> tuple[int, str, str]:
        step, batch = args
        file_blocks: list[str] = []
        for f in batch:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:_PER_FILE_CHARS]
                rel = str(f.relative_to(repo_root))
                lang = f.suffix.lstrip(".") or "text"
                file_blocks.append(f"### {rel}\n```{lang}\n{content}\n```")
            except OSError:
                pass
        if not file_blocks:
            return step, "", ""
        file_labels = ", ".join(str(f.relative_to(repo_root)) for f in batch)
        batch_prompt = (
            f"Frage: {question}\n\n"
            f"Analysiere Quellcode-Batch {step}/{len(batches)} [{file_labels}]:\n\n"
            + "\n\n".join(file_blocks)
            + "\n\nExtrahiere alle relevanten Erkenntnisse zur Frage aus diesem Quellcode-Batch. Kurze, präzise Antwort."
        )
        try:
            answer = generate_text(
                prompt=batch_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
                timeout=timeout_s,
            )
            return step, file_labels, str(answer or "").strip()
        except Exception as exc:
            logging.getLogger(__name__).debug("full_scan batch %d failed: %s", step, exc)
            return step, file_labels, ""

    batch_summaries: list[str] = []
    with _cf.ThreadPoolExecutor(max_workers=parallel_batches) as pool:
        futures = {pool.submit(_run_batch, (i + 1, b)): i for i, b in enumerate(batches)}
        results = [None] * len(batches)
        for fut in _cf.as_completed(futures):
            step, file_labels, batch_answer = fut.result()
            results[step - 1] = (step, file_labels, batch_answer)
    for step, file_labels, batch_answer in (r for r in results if r):
        if batch_answer:
            batch_summaries.append(f"**Batch {step}** [{file_labels}]:\n{batch_answer}")

    trace["batches_completed"] = len(batch_summaries)

    if not batch_summaries:
        trace["error"] = "all_batches_empty"
        return "", trace

    combined = "\n\n---\n\n".join(batch_summaries)
    synthesis_prompt = (
        f"Ursprüngliche Frage: {question}\n\n"
        f"Quellcode-Analyse aus {len(batch_summaries)} Batches "
        f"({len(selected)} Dateien, nur {exts[0]}-Quellcode):\n\n"
        + combined
        + "\n\nErstelle eine vollständige, strukturierte Antwort basierend ausschließlich auf dem analysierten Quellcode."
    )
    try:
        final_answer = generate_text(
            prompt=synthesis_prompt,
            provider=provider,
            model=model,
            history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
            timeout=timeout_s,
        )
        final_answer = str(final_answer or "").strip()
    except Exception as exc:
        logging.getLogger(__name__).debug("full_scan synthesis failed: %s", exc)
        final_answer = ""

    return final_answer, trace


def _spawn_ai_chat_reply(*, user_text: str) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        try:
            provider, model = _resolve_ai_snake_chat_provider()

            # Check for full_scan mode
            try:
                from agent.routes.ai_snake_config import _current_config
                from agent.services.retrieval_profile_service import _is_full_scan_intent
                _cfg = _current_config()
                if _is_full_scan_intent(prompt, "", _cfg):
                    answer, scan_trace = _worker_chat_full_scan(prompt, provider=provider, model=model)
                    files_found = scan_trace.get("files_found", 0)
                    batches_done = scan_trace.get("batches_completed", 0)
                    scan_summary = f"full_scan: {batches_done} Batches, {files_found} Dateien"
                    if not answer:
                        answer = "Full-Scan ergab keine Antwort."
                    if len(answer) > 5800:
                        answer = answer[:5800].rstrip() + "\n\n[gekuerzt]"
                    _append_room_ai_message(text=f"{answer}\n\n[{scan_summary}]")
                    return
            except Exception as exc:
                logging.getLogger(__name__).debug("full_scan check failed, falling back: %s", exc)

            grounded_prompt, has_context, context_summary = _build_grounded_snake_prompt(prompt)
            q = prompt.lower()
            asks_for_concrete_local_facts = any(
                token in q for token in (
                    "konkret", "datei", "dateien", "artefakt", "artefakte", "welche", "verfuegbar", "verfügbar"
                )
            )
            if asks_for_concrete_local_facts and not has_context:
                _append_room_ai_message(text=f"Unklar, bitte Kontext pruefen.\n\n[{context_summary}]")
                return
            answer = generate_text(
                prompt=grounded_prompt,
                provider=provider,
                model=model,
                history=[{"role": "system", "content": _SNAKE_CHAT_PROMPT}],
                timeout=min(int(getattr(settings, "http_timeout", 120) or 120), 180),
            )
            text = str(answer or "").strip()
            asked_for_link = any(token in prompt.lower() for token in ("link", "url", "quelle", "source"))
            if text and not asked_for_link:
                # Remove likely external links unless explicitly requested.
                text = text.replace("http://", "").replace("https://", "")
            if len(text) > 2200:
                text = text[:2200].rstrip() + "\n\n[gekuerzt]"
            if not text:
                text = "AI-Snake konnte gerade keine Antwort erzeugen."
            text = f"{text}\n\n[{context_summary}]"
            _append_room_ai_message(text=text)
        except Exception as exc:
            logging.getLogger(__name__).warning("ai-snake-chat-reply failed: %s", exc)
            _append_room_ai_message(text="AI-Snake Fehler: Antwort konnte nicht erzeugt werden.")

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()


def _is_local_request() -> bool:
    remote = request.remote_addr or ""
    try:
        ip = ip_address(remote)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def _optional_user_auth() -> dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    if token.count(".") != 2:
        alt = str(request.headers.get("X-Ananta-User-Authorization") or "").strip()
        if not alt.startswith("Bearer "):
            return {}
        token = alt[7:].strip()
    if token.count(".") != 2:
        return {}
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30)
        return dict(payload or {})
    except jwt.PyJWTError:
        return {}


def _request_device_id() -> str:
    return str(request.headers.get("X-Ananta-Device-Id") or "").strip()


def _snake_bound_to_auth(snake: dict[str, Any], auth: dict[str, Any]) -> bool:
    user_id = str(auth.get("sub") or auth.get("username") or "").strip()
    if not user_id:
        return False
    snake_user = str(snake.get("oidc_id") or "").strip()
    if snake_user and snake_user != user_id:
        return False
    req_device = _request_device_id()
    snake_device = str(snake.get("owner_device_id") or "").strip()
    if req_device and snake_device and req_device != snake_device:
        return False
    return True


def _next_free_color() -> str:
    used = {s.get("color") for s in _snakes.values()}
    for c in _VALID_COLORS:
        if c not in used:
            return c
    return "mint"


@snakes_bp.route("/snakes", methods=["POST"])
def register_snake():
    """POST /snakes – Snake registrieren. Body: {name, role, color?, oidc_id?}"""
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name erforderlich"}), 400
    role = str(body.get("role") or "viewer")
    if role not in _VALID_ROLES:
        role = "viewer"
    color = str(body.get("color") or "")
    if color not in _VALID_COLORS:
        color = _next_free_color()
    # check color collision
    used_colors = {s["color"] for s in _snakes.values() if s.get("active")}
    if color in used_colors:
        color = _next_free_color()
    auth = _optional_user_auth()
    if not auth and not _is_local_request():
        return jsonify({"error": "oidc_login_required_or_local_dev_only"}), 401
    oidc_id = str(auth.get("sub") or auth.get("username") or "")
    owner_device_id = _request_device_id()

    active_count = sum(1 for s in _snakes.values() if s.get("active"))
    if active_count >= _MAX_SNAKES:
        return jsonify({"error": f"Maximale Snake-Anzahl ({_MAX_SNAKES}) erreicht"}), 409

    snake_id = f"s-{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(32)
    _snakes[snake_id] = {
        "id": snake_id,
        "name": name,
        "role": role,
        "color": color,
        "oidc_id": oidc_id,
        "owner_device_id": owner_device_id,
        "auth_mode": "user_jwt" if auth else "legacy_local_dev",
        "token": token,
        "active": True,
        "registered_at": time.time(),
        "last_heartbeat": time.time(),
    }
    _messages[snake_id] = []
    return jsonify({"id": snake_id, "token": token, "color": color}), 201


@snakes_bp.route("/snakes", methods=["GET"])
def list_snakes():
    """GET /snakes – alle aktiven Snakes auflisten."""
    result = []
    for snake in _snakes.values():
        age = time.time() - float(snake.get("last_heartbeat", 0))
        result.append({
            "id": snake["id"],
            "name": snake["name"],
            "role": snake["role"],
            "color": snake["color"],
            "oidc_id": snake.get("oidc_id") or "",
            "active": bool(snake.get("active")),
            "status": "online" if age < 30 else "offline",
            "last_heartbeat": snake.get("last_heartbeat"),
        })
    return jsonify({"snakes": result}), 200


@snakes_bp.route("/snakes/<snake_id>", methods=["DELETE"])
def deregister_snake(snake_id: str):
    """DELETE /snakes/<id> – Snake abmelden."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    snake["active"] = False
    return jsonify({"ok": True, "id": snake_id}), 200


@snakes_bp.route("/snakes/<snake_id>/heartbeat", methods=["POST"])
def snake_heartbeat(snake_id: str):
    """POST /snakes/<id>/heartbeat – Liveness-Ping."""
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return jsonify({"error": "Snake nicht gefunden oder inaktiv"}), 404
    snake["last_heartbeat"] = time.time()
    return jsonify({"ok": True}), 200


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["POST"])
def send_message(snake_id: str):
    """POST /snakes/<id>/messages – Nachricht an Snake senden. Body: {from_id, text, priority?}"""
    snake = _snakes.get(snake_id)
    if not snake or not snake.get("active"):
        return jsonify({"error": "Ziel-Snake nicht gefunden oder inaktiv"}), 404
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    from_id = str(body.get("from_id") or "unknown")
    text = str(body.get("text") or "").strip()[:200]
    if not text:
        return jsonify({"error": "text erforderlich"}), 400
    priority = int(body.get("priority") or 5)
    msg: dict[str, Any] = {
        "from_id": from_id,
        "text": text,
        "priority": priority,
        "at": time.time(),
    }
    inbox = _messages.setdefault(snake_id, [])
    inbox.append(msg)
    if len(inbox) > 20:
        inbox[:] = inbox[-20:]
    return jsonify({"ok": True}), 202


@snakes_bp.route("/snakes/<snake_id>/messages", methods=["GET"])
def get_messages(snake_id: str):
    """GET /snakes/<id>/messages – Nachrichten abrufen (legacy)."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404
    msgs = _messages.get(snake_id, [])
    _messages[snake_id] = []
    return jsonify({"messages": msgs}), 200


# ── Chat API (T03.01) ─────────────────────────────────────────────────────────


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


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["POST"])
def chat_send(snake_id: str):
    """POST /snakes/<id>/chat/messages – ChatMessage-v1 senden."""
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

    if not text:
        return jsonify({"error": "text erforderlich"}), 400

    # Reject local_only messages
    if visibility == "local_only":
        return jsonify({"error": "local_only Nachrichten werden am Hub abgelehnt"}), 422

    if channel_type not in _VALID_CHANNEL_TYPES:
        return jsonify({"error": f"ungültiger channel_type: {channel_type}"}), 422

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
        global _room_messages
        # deduplicate
        existing_ids = {m["id"] for m in _room_messages}
        if msg["id"] not in existing_ids:
            _room_messages.append(msg)
            if len(_room_messages) > _MAX_ROOM_MSGS:
                _room_messages = _room_messages[-_MAX_ROOM_MSGS:]
            _spawn_ai_chat_reply(user_text=text)
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
    """GET /snakes/<id>/chat/messages?since=<cursor> – Chat-Nachrichten abrufen."""
    snake = _snakes.get(snake_id)
    if not snake:
        return jsonify({"error": "Snake nicht gefunden"}), 404

    since_str = request.args.get("since", "")
    since: float = float(since_str) if since_str else 0.0

    # Collect: direct messages for this snake + room messages
    direct = [m for m in _chat_messages.get(snake_id, []) if float(m.get("created_at") or 0) > since]
    room = [m for m in _room_messages if float(m.get("created_at") or 0) > since and m.get("sender_id") != snake_id]

    all_msgs = sorted(direct + room, key=lambda m: float(m.get("created_at") or 0))

    # Clear delivered direct messages
    if direct:
        _chat_messages[snake_id] = [m for m in _chat_messages.get(snake_id, []) if float(m.get("created_at") or 0) <= since or m in direct and False]
        # actually just clear the ones we returned
        delivered_ids = {m["id"] for m in direct}
        _chat_messages[snake_id] = [m for m in _chat_messages.get(snake_id, []) if m["id"] not in delivered_ids]

    new_cursor = str(time.time()) if all_msgs else since_str

    return jsonify({"messages": all_msgs, "cursor": new_cursor}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/ack", methods=["POST"])
def chat_ack(snake_id: str):
    """POST /snakes/<id>/chat/ack – Gelesene Nachrichten bestätigen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


@snakes_bp.route("/snakes/participants", methods=["GET"])
def list_participants():
    """GET /snakes/participants – Aktive Teilnehmer mit Rolle, Farbe, Status."""
    now = time.time()
    result = []
    for snake in _snakes.values():
        age = now - float(snake.get("last_heartbeat", 0))
        result.append({
            "id": snake["id"],
            "name": snake["name"],
            "role": snake["role"],
            "color": snake["color"],
            "status": "online" if age < 30 else "offline",
            "last_seen": snake.get("last_heartbeat"),
        })
    return jsonify({"participants": result}), 200


# ── Synchrone AI-Ask API (TUI worker mode) ────────────────────────────────────


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
        # If configured model exists in LMStudio, use it directly
        if configured and "smoke" not in configured.lower() and "ananta" not in configured.lower():
            from agent.llm_integration import _find_matching_lmstudio_candidate
            matched = _find_matching_lmstudio_candidate(configured, candidates)
            if matched:
                return str(matched.get("id") or configured)
        # Fall back to best available model
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


@snakes_bp.route("/worker-context", methods=["POST"])
def worker_context():
    """POST /worker-context — CWFH-009: Build WorkerContextHandoffV3 from a question.

    Accepts:
      {
        "question": str,
        "output_dir": str,            # CodeCompass output directory
        "memory_context": str?,
        "manifest_hash": str?,
        "depth": str?,
        "workspace_root": str?,
        "max_candidates": int?        # default 40
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
        from worker.retrieval.codecompass_candidate_resolver import CodeCompassCandidateResolver
        from agent.services.context_file_reader_service import (
            ContextFileReaderService, FileReadPolicy,
        )
        from agent.services.worker_contract_service import get_worker_contract_service
        from agent.services.worker_context_handoff_diagnostics_service import (
            get_worker_context_handoff_diagnostics_service,
        )

        resolver = CodeCompassCandidateResolver(max_candidates=max(1, min(max_candidates, 100)))
        candidates = resolver.resolve(
            question=question,
            output_dir=output_dir,
            memory_context=memory_context,
            manifest_hash=manifest_hash,
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
    """POST /snake/ask – Synchrone AI-Antwort für den TUI ananta-worker Modus.

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
    limits = SnakeAskLimits.from_payload(body)
    retrieval_config_overrides = _snake_retrieval_config_overrides(body)
    # Model from TUI config (passed in v2 payload as "model")
    request_model = str(body.get("model") or "").strip() or None
    if not question:
        return jsonify({"error": "question erforderlich"}), 400

    rag_trace: dict[str, Any] = {}
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
        grounded_prompt, has_context, context_summary = _build_grounded_snake_prompt(
            question,
            limits=limits,
            retrieval_config_overrides=retrieval_config_overrides,
        )
        rag_trace["source"] = "hub_rag"
        rag_trace["has_context"] = has_context
        rag_trace["summary"] = context_summary
        if debug:
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
    # TUI-configured model takes precedence over hub default
    model = request_model or hub_model

    # full_scan mode: multi-batch source-code analysis bypasses standard propose
    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import _is_full_scan_intent
        _eff_cfg = _current_config()
        _eff_cfg.update(dict(retrieval_config_overrides or {}))
        if _is_full_scan_intent(question, "", _eff_cfg):
            answer, worker_trace = _worker_chat_full_scan(question, provider=provider, model=model, limits=limits)
            if answer:
                files_found = worker_trace.get("files_found", 0)
                batches_done = worker_trace.get("batches_completed", 0)
                summary = f"full_scan: {batches_done} Batches, {files_found} Quelldateien"
                if len(answer) > limits.answer_chars:
                    answer = answer[:limits.answer_chars].rstrip() + "\n\n[gekuerzt]"
                resp: dict[str, Any] = {"answer": answer, "path": "full_scan", "context_summary": summary}
                if debug:
                    resp["trace"] = {"worker": worker_trace}
                return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).debug("full_scan routing failed, falling back: %s", exc)

    # Primary path: route through registered ananta-worker
    answer, worker_trace = _worker_propose(
        grounded_prompt,
        model,
        limits=limits,
        retrieval_profile_trace=rag_trace.get("retrieval_profile") if isinstance(rag_trace.get("retrieval_profile"), dict) else None,
    )
    if answer:
        resp = {"answer": answer, "path": "worker"}
        if debug:
            resp["trace"] = {"rag": rag_trace, "worker": worker_trace}
        return jsonify(resp), 200

    # Fallback: direct LMStudio call from hub
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
        resp = {"answer": text, "path": "hub_direct"}
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
        return jsonify(resp), 200
    except Exception as exc:
        logging.getLogger(__name__).warning("snake-ask failed: %s", exc)
        return jsonify({"error": f"LLM-Fehler: {str(exc)[:120]}"}), 503
