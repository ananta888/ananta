import json
import logging

from flask import current_app

from agent.db_models import ConfigDB
from agent.services.repository_registry import get_repository_registry
from typing import Any

logger = logging.getLogger(__name__)

PLAN_FEATURE_FLAGS_KEY = "goal_workflow_feature_flags"


def get_goal_feature_flags() -> dict[str, bool]:
    try:
        from agent.config import settings as _settings
        defaults = {
            "goal_workflow_enabled": bool(getattr(_settings, "goal_workflow_enabled", True)),
            "persisted_plans_enabled": bool(getattr(_settings, "persisted_plans_enabled", True)),
        }
    except Exception:
        defaults = {
            "goal_workflow_enabled": True,
            "persisted_plans_enabled": True,
        }
    stored = get_repository_registry().config_repo.get_by_key(PLAN_FEATURE_FLAGS_KEY)
    try:
        logger.debug(
            "get_goal_feature_flags: defaults=%s, stored=%s", defaults, stored.value_json if stored else None
        )
    except Exception:
        pass

    if not stored:
        return defaults
    try:
        payload = json.loads(stored.value_json or "{}")
        if isinstance(payload, dict):
            merged = {**defaults, **{k: bool(v) for k, v in payload.items()}}
            try:
                logger.debug("merged feature flags: %s", merged)
            except Exception:
                pass
            return merged
    except Exception:
        pass
    return defaults


def set_goal_feature_flags(flags: dict[str, Any]) -> dict[str, bool]:
    merged = {**get_goal_feature_flags(), **{k: bool(v) for k, v in (flags or {}).items()}}
    get_repository_registry().config_repo.save(ConfigDB(key=PLAN_FEATURE_FLAGS_KEY, value_json=json.dumps(merged)))
    return merged


def get_plan_generation_limits() -> dict[str, int]:
    config = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("goal_plan_limits", {}) or {}

    def _safe_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(fallback)

    raw_max_nodes = config.get("max_plan_nodes")
    if raw_max_nodes is None:
        raw_max_nodes = config.get("max_nodes")
    max_nodes = max(1, min(_safe_int(raw_max_nodes, 8), 50))

    raw_max_depth = config.get("max_plan_depth")
    if raw_max_depth is None:
        raw_max_depth = config.get("max_depth")
    max_depth = max(1, min(_safe_int(raw_max_depth, max_nodes), max_nodes))

    return {
        "max_plan_nodes": max_nodes,
        "max_plan_depth": max_depth,
        "max_nodes": max_nodes,
        "max_depth": max_depth,
    }
