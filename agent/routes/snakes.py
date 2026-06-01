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
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from ipaddress import ip_address
from typing import Any

import jwt
from flask import Blueprint, jsonify, request

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


def _build_grounded_snake_prompt(user_text: str) -> tuple[str, bool, str]:
    prompt = str(user_text or "").strip()
    if not prompt:
        return prompt
    try:
        from agent.routes.ai_snake_config import _current_config  # local import avoids route init coupling

        cfg = _current_config()
        use_codecompass = bool(cfg.get("chat_use_codecompass"))
        include_wiki = bool(cfg.get("chat_include_wikipedia"))
        source_types: list[str] = []
        if use_codecompass:
            source_types.append("artifact")
        if include_wiki:
            source_types.append("wiki")
        bundle, grounded = get_rag_service().build_execution_context(
            prompt,
            task_kind="chat",
            retrieval_intent="chat_codecompass",
            source_types=source_types or None,
        )
        chunks = list(bundle.get("chunks") or [])
        if chunks:
            src_counts: dict[str, int] = {}
            for chunk in chunks:
                source = str((chunk or {}).get("source") or "unknown").strip().lower() or "unknown"
                src_counts[source] = int(src_counts.get(source, 0)) + 1
            summary_parts = [f"{k}:{v}" for k, v in sorted(src_counts.items())]
            summary = f"Kontext: {len(chunks)} Treffer ({', '.join(summary_parts)})"
            return grounded, True, summary
    except Exception:
        pass
    return prompt, False, "Kontext: 0 Treffer"


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


def _spawn_ai_chat_reply(*, user_text: str) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return

    def _runner() -> None:
        try:
            provider, model = _resolve_ai_snake_chat_provider()
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
