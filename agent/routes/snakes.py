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

# Cancel events for running full_scan operations keyed by snake_id or "room"
_SCAN_CANCELS: dict[str, threading.Event] = {}

_VALID_ROLES = {"player", "tutor", "critic", "coach", "viewer"}
_VALID_COLORS = {"mint", "amber", "rose", "violet", "sky", "coral", "lime", "ice", "cyan"}


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


# Register routes from sub-modules
from .snakes_config_routes import *  # noqa: F401, F403, E402
from .snakes_execution_routes import *  # noqa: F401, F403, E402
# Re-export private helpers needed by external importers / monkeypatches
from .snakes_execution_routes import (  # noqa: F401, E402
    _spawn_ai_chat_reply,
    _worker_chat_full_scan,
    _pick_worker_for_ask,
    _build_grounded_snake_prompt,
    _snake_retrieval_dry_run,
)
