import time
from typing import Any

from flask import Flask

from agent.config import settings
from agent.config_defaults import build_default_agent_config, merge_db_config_overrides, sync_runtime_state
from agent.routes.system import _load_history


def build_base_app_config(agent: str) -> dict[str, Any]:
    agent_name = settings.agent_name if settings.agent_name != "default" else agent
    return {
        "AGENT_NAME": agent_name,
        "AGENT_TOKEN": settings.agent_token,
        "APP_STARTED_AT": time.time(),
        "PROVIDER_URLS": {
            "ollama": settings.ollama_url,
            "lmstudio": settings.lmstudio_url,
            "openai": settings.openai_url,
            "codex": settings.openai_url,
            "anthropic": settings.anthropic_url,
        },
        "OPENAI_API_KEY": settings.openai_api_key,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "DATA_DIR": settings.data_dir,
        "TASKS_PATH": f"{settings.data_dir}/tasks",
        "AGENTS_PATH": f"{settings.data_dir}/agents",
    }


def initialize_runtime_state(app: Flask) -> dict[str, Any]:
    _load_history(app)
    default_cfg = build_default_agent_config()
    merge_db_config_overrides(default_cfg)
    app.config["AGENT_CONFIG"] = default_cfg
    sync_runtime_state(app, default_cfg)
    return default_cfg
