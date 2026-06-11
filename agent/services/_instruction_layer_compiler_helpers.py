from __future__ import annotations

import os
from typing import Any

from agent.db_models import TaskDB


_CCARI_AGENT_TEMPLATES = frozenset({"opencode", "ananta_worker", "ai_snake_chat"})


def codecompass_runtime_active(task: dict[str, Any] | TaskDB | None) -> bool:
    flag = str(os.environ.get("ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if task is None:
        return False
    if isinstance(task, TaskDB):
        payload = task.model_dump()
    elif isinstance(task, dict):
        payload = task
    else:
        return False
    cc_block = payload.get("codecompass_context")
    if isinstance(cc_block, dict) and cc_block:
        return True
    if isinstance(cc_block, list) and cc_block:
        return True
    template = str(payload.get("agent_template") or "").strip().lower()
    if template in _CCARI_AGENT_TEMPLATES:
        return True
    return False


def codecompass_runtime_trigger(task: dict[str, Any] | TaskDB | None) -> str:
    if str(os.environ.get("ANANTA_CODECOMPASS_RUNTIME_LAYER_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "env_flag"
    if task is None:
        return "unknown"
    if isinstance(task, TaskDB):
        payload = task.model_dump()
    elif isinstance(task, dict):
        payload = task
    else:
        return "unknown"
    cc_block = payload.get("codecompass_context")
    if isinstance(cc_block, (dict, list)) and cc_block:
        return "codecompass_context"
    template = str(payload.get("agent_template") or "").strip().lower()
    if template in _CCARI_AGENT_TEMPLATES:
        return "agent_template"
    return "unknown"


def contains_runtime_override_attempt(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    needles = (
        "disable codecompass runtime",
        "remove codecompass runtime",
        "ignore codecompass runtime",
        "override codecompass runtime",
        "skip codecompass runtime",
        "disable the runtime rules",
        "ignore the runtime rules",
        "remove the runtime rules",
    )
    return any(needle in lowered for needle in needles)
