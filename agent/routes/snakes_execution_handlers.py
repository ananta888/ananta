"""Snake execution endpoint implementations — chat API, ask, worker-context."""
# This module is also the historical ``snakes_execution_routes`` compatibility
# surface (see that module's ``sys.modules`` alias), so selected imports are
# intentionally re-exported even when this implementation does not call them.
# ruff: noqa: F401

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
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from flask import Blueprint, Response, current_app, has_app_context, jsonify, request

from agent.config import settings
from agent.llm_integration import generate_text
from agent.services.openai_credential_endpoint_binding import (
    OpenAICredentialEndpointBindingError,
    bind_openai_credential_endpoint,
)
from agent.services.rag_service import get_rag_service
from agent.services.snake_chat_cancellation import (
    cancel_chat,
    register_chat_cancel,
    unregister_chat_cancel,
)
from agent.services.user_token_scope import SNAKE_EVENTS_STREAM_TOKEN_USE

from .snake_event_broadcaster import (
    broadcast_snake_event,
    drop_snake_queue,
    get_snake_event,
)
from .snakes_chat_helpers import (
    _ANANTA_UI_GUIDE_MAP,
    SnakeAskLimits,
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
from .snakes_chat_reply_runner import _SNAKE_CHAT_PROMPT, SnakeChatReplyRunner
from .snakes_execution_session_helpers import (
    normalize_client_context_history as _normalize_client_context_history,
)
from .snakes_execution_session_helpers import (
    owned_chat_session_snapshot as _owned_chat_session_snapshot,
)
from .snakes_execution_session_helpers import public_snake_message as _public_snake_message
from .snakes_full_scan import _SCAN_CANCELS as _FULL_SCAN_CANCELS
from .snakes_full_scan import worker_chat_full_scan as _worker_chat_full_scan
from .snakes_rag_iterative import worker_chat_rag_iterative as _worker_chat_rag_iterative
from .snakes_retrieval_helpers import (
    _SNAKE_RETRIEVAL_CONFIG_KEYS,
    _build_local_repo_fallback_context,
    _domain_scope_response,
    _resolve_domain_scope_for_chat,
    _resolve_snake_retrieval_profile_trace,
    _snake_retrieval_config_overrides,
    _snake_retrieval_dry_run,
)
from .snakes_state import (
    _MAX_CHAT_MSGS,
    _MAX_ROOM_MSGS,
    _SCAN_CANCELS,
    _VALID_CHANNEL_TYPES,
    _VALID_VISIBILITY,
    _chat_messages,
    _chat_principal_from_auth,
    _check_snake_control_auth,
    _optional_user_auth,
    _request_device_id,
    _room_messages,
    _snake_bound_to_auth,
    _snake_owner_principal,
    _snake_stream_query_auth,
    _snakes,
    snakes_bp,
)
from .snakes_trace_routes import (
    chat_trace_detail,
    chat_trace_events,
    chat_traces_list,
)
from .snakes_trace_routes import (
    trace_owned_snake as _trace_owned_snake,
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
    resolve_snake_routing_task_kind,
    snake_profile_routing_enabled,
)

# In-memory UI state pushed by the browser via PUT /snakes/<id>/ui-state.
# Keyed by snake_id; used to enrich LLM prompts with current navigation context.
_snake_ui_state: dict[str, dict] = {}


def _background_threads_disabled() -> bool:
    return bool(
        (has_app_context() and bool(getattr(current_app, "testing", False)))
        or str(getattr(settings, "role", "")).strip().lower() == "test"
        or os.environ.get("PYTEST_CURRENT_TEST")
        or str(os.environ.get("ANANTA_DISABLE_BACKGROUND_THREADS") or "").strip().lower() in {"1", "true", "yes", "on"}
    )


def _resolve_ai_snake_chat_provider(config: dict[str, Any] | None = None) -> tuple[str, str | None, str | None]:
    provider = "lmstudio"
    model: str | None = None
    api_base: str | None = None
    cfg: dict[str, Any] = {}
    try:
        from agent.routes.ai_snake_config import _current_config

        cfg = dict(config) if config is not None else _current_config()
        configured_backend = str(cfg.get("chat_backend") or "").strip().lower()
        configured_model = str(cfg.get("chat_backend_model") or "").strip() or None
        configured_api_base = str(cfg.get("chat_backend_api_base") or "").strip() or None
        if configured_model:
            model = configured_model

        _openai_models = ("gpt-4", "gpt-3.5", "gpt-4o", "o1", "o3")
        is_openai_model = any(model.startswith(m) for m in _openai_models) if model else False
        is_openai_url = configured_api_base and "openai.com" in configured_api_base.lower()

        def _chat_completions_url(base_url: str) -> str:
            normalized = base_url.rstrip("/")
            return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"

        if configured_backend in {"openai", "codex"} or (
            not configured_backend and (is_openai_url or is_openai_model)
        ):
            provider = "openai"
            api_base = bind_openai_credential_endpoint(
                client_api_base=configured_api_base,
                trusted_api_url=str(settings.openai_url),
                credential_ref=str(cfg.get("chat_backend_credential_ref") or ""),
            ).chat_completions_url
        elif configured_backend in {"ollama", "lmstudio"}:
            provider = configured_backend
            if configured_api_base:
                api_base = _chat_completions_url(configured_api_base)
    except OpenAICredentialEndpointBindingError:
        raise
    except Exception:
        pass

    central = _resolve_central_ai_snake_model(cfg)
    if central is not None:
        provider, model = central.provider_id, central.model_id
        configured_api_base = central.base_url
        if provider == "openai":
            api_base = bind_openai_credential_endpoint(
                client_api_base=configured_api_base,
                trusted_api_url=str(settings.openai_url),
                credential_ref=str(cfg.get("chat_backend_credential_ref") or ""),
            ).chat_completions_url
        elif configured_api_base:
            normalized = configured_api_base.rstrip("/")
            api_base = (
                normalized
                if normalized.endswith("/chat/completions")
                else f"{normalized}/chat/completions"
            )
    return provider, model, api_base


def _resolve_central_ai_snake_model(config: dict[str, Any]):
    """Resolve an explicit central assignment; otherwise preserve legacy config."""

    if not has_app_context():
        return None
    from agent.services.model_runtime_selection_service import (
        ModelRuntimeSelectionError,
        resolve_explicit_hub_model,
    )
    from ananta_contracts.model_selection import ModelRoutingDryRunCommand

    configured_backend = str(config.get("chat_backend") or "").strip().lower()
    try:
        return resolve_explicit_hub_model(ModelRoutingDryRunCommand(
            consumer_id="chat.ai_snake",
            requires_streaming=True,
            allow_cloud=configured_backend in {"openai", "codex", "openrouter"},
        ))
    except ModelRuntimeSelectionError:
        raise
    except Exception as exc:
        current_app.logger.warning(
            "Central AI-Snake model route unavailable; using legacy fallback: %s",
            type(exc).__name__,
        )
        return None


# Defaults defer to this module's (monkeypatchable) names at call time.
_chat_reply_runner = SnakeChatReplyRunner(
    ui_state=_snake_ui_state,
    resolve_chat_provider=lambda config: _resolve_ai_snake_chat_provider(config),
    append_room_message=lambda **kwargs: _append_room_ai_message(**kwargs),
    worker_propose=lambda *args, **kwargs: _worker_propose(*args, **kwargs),
    worker_picker_provider=lambda: _pick_worker_for_ask,
    generate_text=lambda **kwargs: generate_text(**kwargs),
    logger=logging.getLogger(__name__),
)


def _spawn_ai_chat_reply(
    *,
    user_text: str,
    snake_id: str | None = None,
    ui_context: dict | None = None,
    client_session_id: str = "",
    context_history: list[dict[str, str]] | None = None,
    session_snapshot: dict[str, Any] | None = None,
    owner_principal: dict[str, str] | None = None,
) -> None:
    prompt = str(user_text or "").strip()
    if not prompt:
        return
    if _background_threads_disabled():
        return

    def _runner() -> None:
        _chat_reply_runner.run(
            prompt=prompt,
            snake_id=snake_id,
            ui_context=ui_context,
            client_session_id=client_session_id,
            context_history=context_history,
            session_snapshot=session_snapshot,
            owner_principal=owner_principal,
        )

    thread = threading.Thread(target=_runner, name="snake-chat-reply", daemon=True)
    thread.start()


# ── Route endpoints ────────────────────────────────────────────────────────────


@snakes_bp.route("/snakes/<snake_id>/chat/messages", methods=["POST"])
def chat_send(snake_id: str):
    """POST /snakes/<id>/chat/messages -- ChatMessage-v1 senden."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    auth = _optional_user_auth()
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    snake = _snakes.get(snake_id) or {}
    if not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    principal = _chat_principal_from_auth(auth)
    if principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401

    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    channel_type = str(body.get("channel_type") or "room")
    visibility = str(body.get("visibility") or "room")
    text = str(body.get("text") or "").strip()[:500]
    ui_context = body.get("ui_context") or {}
    # session_id sent by the frontend reflects the panel's active session, bypassing user.json race conditions
    client_session_id = str(body.get("session_id") or "").strip()
    if channel_type in {"room", "direct"} and not client_session_id:
        return jsonify({"error": "chat_session_required", "error_code": "chat_session_required"}), 400
    session_snapshot = (
        _owned_chat_session_snapshot(client_session_id, principal)
        if client_session_id
        else None
    )
    if client_session_id and session_snapshot is None:
        return jsonify({"error": "chat_session_not_found", "error_code": "chat_session_not_found"}), 404
    try:
        context_history = _normalize_client_context_history(body.get("context_history"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

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
            _append_visual_user_tick(
                ui_snapshot=_ui_snap,
                snake_id=snake_id,
                owner_principal=principal.to_dict(),
            )
            # VG-053: submit to ThreadPoolExecutor instead of daemon thread
            _VISUAL_GUIDE_EXECUTOR.submit(
                _spawn_visual_reply,
                _ui_snap,
                snake_id,
                principal.to_dict(),
            )
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
                owner_principal=principal.to_dict(),
            )
            return jsonify({"ok": True, "id": str(body.get("id") or "")}), 202

        _append_room_ai_message(
            text=text[:500],
            session_id=_VISUAL_SESSION_ID,
            visibility="system",
            sender_id="browser",
            owner_principal=principal.to_dict(),
        )
        _region_steps = list((ui_context or {}).get("region_steps") or [])
        _region_route = str((ui_context or {}).get("route") or "").strip()
        if _region_steps:
            # VG-053: submit to ThreadPoolExecutor instead of daemon thread
            _VISUAL_GUIDE_EXECUTOR.submit(
                _spawn_region_explain_reply,
                _region_steps,
                _region_route,
                snake_id,
                principal.to_dict(),
            )
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
        "owner_principal": principal.to_dict(),
    }

    if channel_type == "room":
        global _room_messages  # noqa: PLW0602
        existing_ids = {m["id"] for m in _room_messages}
        if msg["id"] not in existing_ids:
            _room_messages.append(msg)
            if len(_room_messages) > _MAX_ROOM_MSGS:
                _room_messages = _room_messages[-_MAX_ROOM_MSGS:]
            _spawn_ai_chat_reply(
                user_text=text,
                snake_id=snake_id,
                ui_context=ui_context,
                client_session_id=client_session_id,
                context_history=context_history,
                session_snapshot=session_snapshot,
                owner_principal=principal.to_dict(),
            )
    elif channel_type == "direct":
        target_ids = msg["target_ids"]
        if not target_ids:
            return jsonify({"error": "target_ids erforderlich für direct"}), 422
        target_id = str(target_ids[0])
        target_snake = _snakes.get(target_id)
        if target_snake is None or _snake_owner_principal(target_snake) != principal:
            return jsonify({"error": "target_snake_not_found", "error_code": "target_snake_not_found"}), 404
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
    auth = _optional_user_auth()
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    if not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    principal = _chat_principal_from_auth(auth)
    if principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401

    since_str = request.args.get("since", "")
    try:
        since = float(since_str) if since_str else 0.0
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_cursor", "error_code": "invalid_cursor"}), 400
    if since < 0 or since == float("inf") or since != since:
        return jsonify({"error": "invalid_cursor", "error_code": "invalid_cursor"}), 400
    requested_session_id = str(request.args.get("session_id") or "").strip()
    if not requested_session_id:
        return jsonify({"error": "chat_session_required", "error_code": "chat_session_required"}), 400
    if _owned_chat_session_snapshot(requested_session_id, principal) is None:
        return jsonify({"error": "chat_session_not_found", "error_code": "chat_session_not_found"}), 404

    expected_owner = principal.to_dict()

    def _message_is_visible(message: dict[str, Any]) -> bool:
        if str(message.get("session_id") or "") != requested_session_id:
            return False
        raw_owner = message.get("owner_principal")
        # Legacy rows are safe only after exact authorization of their globally
        # unique session above. New rows always carry the canonical principal.
        return raw_owner == expected_owner if isinstance(raw_owner, dict) else True

    direct = [
        m
        for m in _chat_messages.get(snake_id, [])
        if float(m.get("created_at") or 0) > since and _message_is_visible(m)
    ]
    room = [
        m for m in _room_messages
        if float(m.get("created_at") or 0) > since
        and _message_is_visible(m)
    ]

    all_msgs = sorted(direct + room, key=lambda m: float(m.get("created_at") or 0))

    # GET is intentionally non-draining. A read by one browser/device must not
    # destroy another authorized consumer's direct-message history.
    new_cursor = str(max(float(m.get("created_at") or 0) for m in all_msgs)) if all_msgs else since_str

    return jsonify({"messages": [_public_snake_message(m) for m in all_msgs], "cursor": new_cursor}), 200


@snakes_bp.route("/snakes/<snake_id>/chat/cancel", methods=["POST"])
def chat_cancel(snake_id: str):
    """POST /snakes/<id>/chat/cancel -- Laufenden AI-Snake-Chat abbrechen."""
    if not _verify_token(snake_id):
        return jsonify({"error": "Ungültiger Token"}), 401
    auth = _optional_user_auth()
    snake = _snakes.get(snake_id)
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
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
    auth = _optional_user_auth()
    snake = _snakes.get(snake_id)
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    body: dict[str, Any] = request.get_json(force=True, silent=True) or {}
    message_ids: list[str] = [str(i) for i in (body.get("message_ids") or [])]
    return jsonify({"ok": True, "acked": len(message_ids)}), 200


@snakes_bp.route("/snakes/<snake_id>/events/stream-token", methods=["POST"])
def create_snake_events_stream_token(snake_id: str):
    """Mint a short-lived user/tenant/Snake-bound SSE credential."""

    auth = _optional_user_auth()
    snake = _snakes.get(snake_id)
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
    principal = _chat_principal_from_auth(auth)
    if principal is None:
        return jsonify({"error": "canonical_identity_required"}), 401
    issued_at = int(time.time())
    expires_at = issued_at + 60
    stream_token = jwt.encode(
        {
            "sub": principal.subject_id,
            "tenant_id": principal.tenant_id,
            "role": str(auth.get("role") or "user"),
            "token_use": SNAKE_EVENTS_STREAM_TOKEN_USE,
            "stream_user_id": principal.subject_id,
            "stream_tenant_id": principal.tenant_id,
            "stream_snake_id": snake_id,
            "iat": issued_at,
            "exp": expires_at,
        },
        settings.secret_key,
        algorithm="HS256",
    )
    return jsonify(
        {
            "stream_token": stream_token,
            "expires_at": expires_at,
            "ttl_seconds": 60,
        }
    ), 201


@snakes_bp.route("/snakes/<snake_id>/events/stream", methods=["GET"])
def snake_events_stream(snake_id: str):
    """GET /snakes/<id>/events/stream -- Server-Sent Events for snake events.

    Streams typed events generated by backend components (e.g. visual guide
    actions, candidate lists).  Polling remains available as a fallback.
    The stream sends a keep-alive comment every ~15s and closes gracefully
    when the snake is deleted or the client disconnects.
    """
    # Never accept the long-lived Snake bearer in URLs. Browser EventSource
    # clients exchange their normal user JWT for a 60-second purpose token.
    if request.args.get("token") is not None:
        return jsonify({"error": "legacy_query_credential_forbidden"}), 401
    auth = _optional_user_auth() or _snake_stream_query_auth(snake_id)
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    snake = _snakes.get(snake_id)
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404

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
    auth = _optional_user_auth()
    snake = _snakes.get(snake_id)
    if not auth:
        return jsonify({"error": "user_authentication_required"}), 401
    if snake is None or not _snake_bound_to_auth(snake, auth):
        return jsonify({"error": "snake_not_found", "error_code": "snake_not_found"}), 404
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
@_check_snake_control_auth
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

    from agent.services.worker_context_path_policy import (
        WorkerContextPathPolicy,
        WorkerContextPathPolicyError,
    )

    try:
        path_policy = WorkerContextPathPolicy.from_value(settings.hub_workspace_root)
        resolved_output_dir = path_policy.resolve_directory(
            output_dir,
            field_name="output_dir",
        )
        resolved_workspace_root = path_policy.resolve_directory(
            workspace_root or output_dir,
            field_name="workspace_root",
        )
    except WorkerContextPathPolicyError as exc:
        return jsonify(
            {
                "error": "worker_context_path_rejected",
                "reason_code": exc.reason_code,
                "field": exc.field_name,
            }
        ), 400

    try:
        from agent.services.context_file_reader_service import (
            ContextFileReaderService,
            FileReadPolicy,
        )
        from agent.services.worker_context_handoff_diagnostics_service import (
            get_worker_context_handoff_diagnostics_service,
        )
        from agent.services.worker_contract_service import get_worker_contract_service
        from ananta_codecompass.candidate_resolver import (
            CodeCompassCandidateResolver,
            ResolverConfig,
        )

        resolver = CodeCompassCandidateResolver(max_candidates=max(1, min(max_candidates, 100)))
        mode = ResolverConfig.from_env()
        candidates = resolver.resolve(
            question=question,
            output_dir=str(resolved_output_dir),
            memory_context=memory_context,
            manifest_hash=manifest_hash,
            mode=mode,
        )

        policy = FileReadPolicy(workspace_root=str(resolved_workspace_root))
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
@_check_snake_control_auth
def snake_ask():
    """POST /snake/ask -- Synchrone AI-Antwort für den TUI ananta-worker Modus.

    Akzeptiert v1 ({question, context, depth}) und v2 ({question, context, depth, memory_context}).
    Optionales Feld "debug": true gibt trace-Infos zurück.
    Antwortet mit {"answer": "..."}. Routet über einen registrierten Worker-Prozess;
    fällt auf direkten LMStudio-Aufruf zurück falls kein Worker verfügbar.
    """
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

    try:
        provider, hub_model, api_base = _resolve_ai_snake_chat_provider()
    except OpenAICredentialEndpointBindingError as exc:
        return jsonify({"error": "provider_configuration_invalid", "error_code": exc.error_code}), 503
    model = request_model or hub_model

    try:
        from agent.routes.ai_snake_config import _current_config
        from agent.services.retrieval_profile_service import _is_full_scan_intent, _is_rag_iterative_intent

        _eff_cfg = _current_config()
        _eff_cfg.update(dict(retrieval_config_overrides or {}))
        # `/snake/ask` has no authenticated chat-session binding. It therefore
        # must not consume the process-global active session or its prompt.
        _active_session_prompt: str | None = None
        if _is_rag_iterative_intent(_eff_cfg):
            _cancel_keys = ["snake_ask"]
            _cancel_event = register_chat_cancel(_cancel_keys)
            try:
                answer, worker_trace = _worker_chat_rag_iterative(
                    question,
                    provider=provider,
                    model=model,
                    api_base=api_base,
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
        allow_profile_routing=request_model is None,
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
