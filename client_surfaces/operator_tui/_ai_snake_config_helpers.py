"""Internal sub-module of ai_snake_config_view.py.

Extracted to keep the main module small. This module owns: Model discovery
helpers, config persistence, env propagation, and shared constants.

Public re-exports: the parent ai_snake_config_view module imports and
re-exports everything so consumers keep working.
"""

from __future__ import annotations

import os
from typing import Any


_PERSISTENT_TUI_CONFIG_KEYS = {
    "tutorial_mode",
    "chat_panel_open",
    "ai_snake_provider_preference",
    "ai_visual_use_codecompass",
    "chat_backend",
    "chat_backend_model",
    "chat_backend_api_base",
    "chat_ask_timeout_s",
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_include_task_memory",
    "chat_source_pack_id",
    "chat_context_chars",
    "chat_max_tokens",
    "chat_rag_top_k",
    "chat_answer_chars",
    # Memory settings (CMW-012)
    "chat_use_history",
    "chat_history_turns",
    "chat_history_chars",
    "chat_use_summary",
    "chat_summary_chars",
    "chat_summary_update_every_turns",
    "chat_pass_memory_to_worker",
    "chat_worker_mode",
    "chat_backend_fallback",
    "chat_include_runtime_status",
    # Input history
    "input_history_chat_enabled",
    "input_history_command_enabled",
    "input_history_max_entries",
    # Advanced chat configuration (env-mapped features)
    "chat_system_prompt",
    "chat_streaming",
    "chat_use_embedding_api",
    "chat_embedding_model",
    "chat_embedding_api_max_records",
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_codecompass_trigger_mode",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_full_scan_source_only",
    "chat_full_scan_max_batches",
    "chat_full_scan_files_per_batch",
    "chat_full_scan_parallel_batches",
    "chat_full_scan_timeout_s",
    "chat_full_scan_chars_per_file",
    "chat_full_scan_max_input_tokens",
}

# Mapping from game-state key to the ANANTA_TUI_CHAT_* env var the
# consumer code reads. Keep in sync with the consumer call-sites in
# chat_mixin.py and tutorial_ai_mixin.py.
_ADVANCED_CHAT_ENV_MAP: dict[str, str] = {
    "chat_system_prompt": "ANANTA_TUI_CHAT_SYSTEM_PROMPT",
    "chat_streaming": "ANANTA_TUI_CHAT_STREAMING",
    "chat_use_embedding_api": "ANANTA_TUI_CHAT_USE_EMBEDDING_API",
    "chat_embedding_model": "ANANTA_TUI_CHAT_EMBEDDING_MODEL",
    "chat_embedding_api_max_records": "ANANTA_TUI_CHAT_EMBEDDING_API_MAX_RECORDS",
}


def _append_unique(values: list[str], candidate: str) -> None:
    item = str(candidate or "").strip()
    if item and item != "-" and item not in values:
        values.append(item)


def _default_models_for_backend(backend: str, game: dict[str, object]) -> list[str]:
    normalized = str(backend or "").strip().lower()
    defaults: list[str] = []
    _append_unique(defaults, str(game.get("chat_backend_model") or ""))
    _append_unique(defaults, str(os.environ.get("ANANTA_TUI_CHAT_MODEL") or ""))
    _append_unique(defaults, str(os.environ.get("ANANTA_TUI_SNAKE_AI_MODEL") or ""))
    if normalized in {"opencode"}:
        _append_unique(defaults, str(os.environ.get("OPENCODE_DEFAULT_MODEL") or ""))
        _append_unique(defaults, "opencode/glm-5-free")
    if normalized in {"ananta-worker", "worker", "hub", "default", "auto"}:
        _append_unique(defaults, "google/gemma-4-e4b")
    if normalized in {"hermes"}:
        _append_unique(defaults, "ananta-smoke")
    return defaults


def _resolve_bool_pref(game: dict[str, object], key: str, env_key: str, default: bool) -> bool:
    value = game.get(key)
    if isinstance(value, bool):
        return value
    token = str(os.environ.get(env_key, "1" if default else "0")).strip().lower()
    enabled = token not in {"0", "false", "no", "off"}
    return enabled if default else token in {"1", "true", "yes", "on"}


def _resolve_text_pref(game: dict[str, object], key: str, env_key: str, default: str) -> str:
    """Resolve a text preference: game state first, then env var, then default."""
    value = game.get(key)
    if isinstance(value, str):
        return value
    return str(os.environ.get(env_key, default))


def _lmstudio_base_candidates() -> list[str]:
    values: list[str] = []
    for candidate in (
        os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL"),
        os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL"),
        os.environ.get("LMSTUDIO_URL"),
        os.environ.get("E2E_LMSTUDIO_URL"),
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://host.docker.internal:1234/v1",
        "http://192.168.178.100:1234/v1",
        "http://192.168.56.1:1234/v1",
    ):
        normalized = str(candidate or "").strip().rstrip("/")
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _model_id_from_row(row: dict[str, Any]) -> str:
    candidates = (
        row.get("id"),
        row.get("model"),
        row.get("model_id"),
        row.get("modelId"),
        row.get("model_key"),
        row.get("modelKey"),
        row.get("identifier"),
        row.get("name"),
    )
    for candidate in candidates:
        model_id = str(candidate or "").strip()
        if model_id:
            return model_id
    return ""


def _model_loaded_state_from_row(row: dict[str, Any]) -> bool | None:
    direct = row.get("loaded")
    if isinstance(direct, bool):
        return direct
    for key in ("is_loaded", "isLoaded"):
        token = row.get(key)
        if isinstance(token, bool):
            return token
    for key in ("state", "status"):
        token = str(row.get(key) or "").strip().lower()
        if token in {"loaded", "active", "ready", "running"}:
            return True
        if token in {"not_loaded", "not-loaded", "unloaded", "idle", "stopped"}:
            return False
    return None


def _set_model_state(states: dict[str, str], model_id: str, loaded: bool | None) -> None:
    key = str(model_id or "").strip()
    if not key:
        return
    state = "unknown"
    if loaded is True:
        state = "loaded"
    elif loaded is False:
        state = "not_loaded"
    current = str(states.get(key) or "")
    if current == "loaded":
        return
    if current == "not_loaded" and state == "unknown":
        return
    states[key] = state


def chat_model_option_label(game: dict[str, object], option: str) -> str:
    model_id = str(option or "").strip()
    if not model_id:
        return "-"
    states_raw = game.get("chat_backend_model_states")
    states = dict(states_raw) if isinstance(states_raw, dict) else {}
    state = str(states.get(model_id) or "unknown").strip().lower()
    if state == "loaded":
        return f"{model_id} [geladen]"
    if state == "not_loaded":
        return f"{model_id} [nicht geladen]"
    return f"{model_id} [status unbekannt]"


def _persist_tui_chat_settings(game: dict[str, object]) -> None:
    payload: dict[str, Any] = {}
    for key in _PERSISTENT_TUI_CONFIG_KEYS:
        value = game.get(key)
        if isinstance(value, (str, int, float, bool)):
            payload[key] = value
    try:
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        get_manager().save(payload)
    except Exception:
        pass
    from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings
    save_tui_chat_settings(payload)
    _propagate_advanced_chat_to_env(game, override_existing=True)


def _propagate_advanced_chat_to_env(
    game: dict[str, object],
    *,
    override_existing: bool = False,
) -> None:
    try:
        from client_surfaces.operator_tui.config.user_config_manager import (
            _DEFAULTS as _ADV_CHAT_DEFAULTS,
        )
    except ImportError:
        _ADV_CHAT_DEFAULTS = {}

    for game_key, env_key in _ADVANCED_CHAT_ENV_MAP.items():
        if game_key not in game:
            continue
        value = game[game_key]
        default = _ADV_CHAT_DEFAULTS.get(game_key)
        if value == default:
            continue
        if not override_existing and env_key in os.environ:
            continue
        if isinstance(value, bool):
            os.environ[env_key] = "1" if value else "0"
        elif isinstance(value, (int, float)):
            os.environ[env_key] = str(int(value))
        elif isinstance(value, str):
            if value:
                os.environ[env_key] = value
