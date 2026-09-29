"""Shared ``visual_process`` blueprint; route modules register their views on it."""

from __future__ import annotations

from flask import Blueprint

vp_bp = Blueprint("visual_process", "agent.routes.visual_process", url_prefix="/api/visual-process")
