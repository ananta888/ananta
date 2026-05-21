from __future__ import annotations

import logging

from agent.config import settings
from agent.services.planning_learning_loop_service import start_planning_learning_loop_thread


def _planning_learning_enabled(app) -> bool:
    agent_cfg = app.config.get("AGENT_CONFIG") or {}
    planning_policy = agent_cfg.get("planning_policy") if isinstance(agent_cfg.get("planning_policy"), dict) else {}
    learning_loop = planning_policy.get("learning_loop") if isinstance(planning_policy.get("learning_loop"), dict) else {}
    return bool(learning_loop.get("enabled", False))


def start_planning_learning_thread(app):
    if settings.role != "hub":
        logging.info("Planning-Learning-Loop uebersprungen (nur Hub).")
        return
    if not _planning_learning_enabled(app):
        logging.info("Planning-Learning-Loop deaktiviert durch Config.")
        return
    start_planning_learning_loop_thread(app)
