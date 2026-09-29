"""Shared ``chat_api`` blueprint; route modules register their views on it."""

from __future__ import annotations

from flask import Blueprint

chat_bp = Blueprint("chat_api", "agent.routes.chat", url_prefix="/api/chat")
