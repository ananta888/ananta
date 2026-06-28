"""Shared hub-owned state and request policy for snake route modules."""
from __future__ import annotations

import threading
from ipaddress import ip_address
from typing import Any

import jwt
from flask import Blueprint, request

from agent.config import settings

snakes_bp = Blueprint("snakes", __name__)

_MAX_SNAKES = 8
_snakes: dict[str, dict[str, Any]] = {}
_messages: dict[str, list[dict[str, Any]]] = {}
_chat_messages: dict[str, list[dict[str, Any]]] = {}
_room_messages: list[dict[str, Any]] = []
_MAX_CHAT_MSGS = 500
_MAX_ROOM_MSGS = 200
_VALID_CHANNEL_TYPES = {"room", "direct", "ai", "system"}
_VALID_VISIBILITY = {"room", "direct", "ai_context", "system"}
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
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if token.count(".") != 2:
        alt = str(request.headers.get("X-Ananta-User-Authorization") or "").strip()
        if not alt.startswith("Bearer "):
            return {}
        token = alt[7:].strip()
    if token.count(".") != 2:
        return {}
    try:
        return dict(jwt.decode(token, settings.secret_key, algorithms=["HS256"], leeway=30) or {})
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
    return not (req_device and snake_device and req_device != snake_device)


def _next_free_color() -> str:
    used = {snake.get("color") for snake in _snakes.values()}
    return next((color for color in _VALID_COLORS if color not in used), "mint")
