from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from client_surfaces.operator_tui.snake_persistence import save_tui_chat_settings


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
    # Primary: atomic write via UserConfigManager (project-scoped user.json)
    try:
        from client_surfaces.operator_tui.config.user_config_manager import get_manager
        get_manager().save(payload)
    except Exception:
        pass
    # Fallback: legacy tui_chat_settings.json for backward compatibility
    save_tui_chat_settings(payload)


def ai_snake_config_items(game: dict[str, object]) -> list[dict[str, object]]:
    try:
        from client_surfaces.operator_tui.config.user_config_manager import _DEFAULTS
    except ImportError:
        _DEFAULTS = {}

    backends_raw = game.get("chat_backends_available")
    chat_backends = [str(item).strip() for item in backends_raw] if isinstance(backends_raw, list) else []
    chat_backends = [item for item in chat_backends if item]
    if not chat_backends:
        chat_backends = ["ananta-worker", "opencode", "lmstudio", "hermes"]

    models_raw = game.get("chat_backend_models")
    chat_models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
    chat_models = [item for item in chat_models if item]
    current_model = str(game.get("chat_backend_model") or "").strip()
    if current_model and current_model not in chat_models:
        chat_models.insert(0, current_model)

    visual_providers = ["lmstudio", "opencode", "hermes", "worker-propose"]
    visual_provider = str(game.get("ai_snake_provider_preference") or "lmstudio").strip()
    if visual_provider and visual_provider not in visual_providers:
        visual_providers.append(visual_provider)

    chat_open = bool(game.get("chat_panel_open"))
    visual_on = bool(game.get("tutorial_mode"))
    codecompass_on = bool(game.get("ai_visual_use_codecompass"))
    chat_use_codecompass = _resolve_bool_pref(game, "chat_use_codecompass", "ANANTA_TUI_CHAT_USE_CODECOMPASS", True)
    chat_include_local_project = _resolve_bool_pref(game, "chat_include_local_project", "ANANTA_TUI_CHAT_INCLUDE_LOCAL_PROJECT", True)
    chat_include_wikipedia = _resolve_bool_pref(game, "chat_include_wikipedia", "ANANTA_TUI_CHAT_INCLUDE_WIKIPEDIA", False)
    chat_source_pack_id = str(
        game.get("chat_source_pack_id")
        or os.environ.get("ANANTA_TUI_CHAT_SOURCE_PACK")
        or "ananta-dev-default"
    ).strip()
    timeout_default = _DEFAULTS.get("chat_ask_timeout_s", 45.0)
    timeout_value_raw = game.get("chat_ask_timeout_s")
    try:
        timeout_value = float(timeout_value_raw) if timeout_value_raw is not None else timeout_default
    except (TypeError, ValueError):
        timeout_value = timeout_default
    timeout_value = max(3.0, min(180.0, timeout_value))
    timeout_options = ["20", "30", "45", "60", "90", "120", "180"]
    chat_api_base = str(game.get("chat_backend_api_base") or "http://localhost:1234/v1").strip()
    chat_api_base_options = [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.178.100:1234/v1",
    ]
    if chat_api_base and chat_api_base not in chat_api_base_options:
        chat_api_base_options.insert(0, chat_api_base)
    context_default = int(_DEFAULTS.get("chat_context_chars", 3000))
    context_chars_raw = game.get("chat_context_chars")
    try:
        context_chars = int(context_chars_raw) if context_chars_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_CONTEXT_CHARS", str(context_default)))
    except (TypeError, ValueError):
        context_chars = context_default
    context_chars = max(500, min(20000, context_chars))
    max_tokens_default = int(_DEFAULTS.get("chat_max_tokens", 400))
    max_tokens_raw = game.get("chat_max_tokens")
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_MAX_TOKENS", str(max_tokens_default)))
    except (TypeError, ValueError):
        max_tokens = max_tokens_default
    max_tokens = max(100, min(8000, max_tokens))
    top_k_default = int(_DEFAULTS.get("chat_rag_top_k", 24))
    top_k_raw = game.get("chat_rag_top_k")
    try:
        chat_rag_top_k = int(top_k_raw) if top_k_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_RAG_TOP_K", str(top_k_default)))
    except (TypeError, ValueError):
        chat_rag_top_k = top_k_default
    chat_rag_top_k = max(8, min(120, chat_rag_top_k))
    answer_chars_raw = game.get("chat_answer_chars")
    try:
        chat_answer_chars = int(answer_chars_raw) if answer_chars_raw is not None else int(os.environ.get("ANANTA_TUI_CHAT_ANSWER_CHARS", "6000"))
    except (TypeError, ValueError):
        chat_answer_chars = 6000
    chat_answer_chars = max(600, min(12000, chat_answer_chars))
    return [
        {"key": "visual_enabled", "label": "Visual AI-Snake", "type": "bool", "value": visual_on},
        {"key": "chat_panel_open", "label": "AI-Chat Panel", "type": "bool", "value": chat_open},
        {"key": "visual_provider", "label": "Visual Provider", "type": "choice", "value": visual_provider, "options": visual_providers},
        {"key": "visual_codecompass", "label": "Visual CodeCompass", "type": "bool", "value": codecompass_on},
        {
            "key": "chat_backend",
            "label": "Chat Provider",
            "type": "choice",
            "value": str(game.get("chat_backend") or "ananta-worker"),
            "options": chat_backends,
        },
        {
            "key": "chat_model",
            "label": "Chat Model",
            "type": "choice",
            "value": str(game.get("chat_backend_model") or "-"),
            "options": chat_models,
        },
        {
            "key": "chat_api_base",
            "label": "Chat API Base",
            "type": "choice",
            "value": chat_api_base,
            "options": chat_api_base_options,
        },
        {
            "key": "chat_ask_timeout_s",
            "label": "Chat Ask Timeout (s)",
            "type": "choice",
            "value": f"{timeout_value:g}",
            "options": timeout_options,
        },
        {"key": "chat_use_codecompass", "label": "Chat CodeCompass", "type": "bool", "value": chat_use_codecompass},
        {"key": "chat_include_local_project", "label": "Chat Local Project", "type": "bool", "value": chat_include_local_project},
        {"key": "chat_include_wikipedia", "label": "Chat Wikipedia", "type": "bool", "value": chat_include_wikipedia},
        {
            "key": "chat_source_pack_id",
            "label": "Chat Source Pack",
            "type": "choice",
            "value": chat_source_pack_id or "ananta-dev-default",
            "options": ["ananta-dev-default", "ananta-default", "ananta-local-only"],
        },
        {
            "key": "chat_context_chars",
            "label": "Chat Context Chars",
            "type": "choice",
            "value": str(context_chars),
            "options": ["1000", "2000", "3000", "5000", "8000", "12000"],
        },
        {
            "key": "chat_max_tokens",
            "label": "Chat Max Tokens",
            "type": "choice",
            "value": str(max_tokens),
            "options": ["400", "800", "1200", "2000", "4000", "8000"],
        },
        {
            "key": "chat_rag_top_k",
            "label": "Chat RAG Top-K",
            "type": "choice",
            "value": str(chat_rag_top_k),
            "options": ["12", "24", "32", "48", "64", "96", "120"],
        },
        {
            "key": "chat_answer_chars",
            "label": "Chat Answer Chars",
            "type": "choice",
            "value": str(chat_answer_chars),
            "options": ["600", "1200", "2400", "4000", "6000", "8000", "12000"],
        },
        # ── Chat Context/Memory (CMW-012) ─────────────────────────────────────
        {"key": "chat_use_history", "label": "Chat Verlauf nutzen", "type": "bool",
         "value": _resolve_bool_pref(game, "chat_use_history", "", True), "group": "Chat Memory"},
        {
            "key": "chat_history_turns",
            "label": "Chat History Turns",
            "type": "choice",
            "value": str(max(1, min(30, int(game.get("chat_history_turns") or 6)))),
            "options": ["3", "6", "10", "15", "20", "30"],
            "group": "Chat Memory",
        },
        {
            "key": "chat_history_chars",
            "label": "Chat History Chars",
            "type": "choice",
            "value": str(max(100, min(10000, int(game.get("chat_history_chars") or 1800)))),
            "options": ["600", "1200", "1800", "3000", "5000"],
            "group": "Chat Memory",
        },
        {"key": "chat_use_summary", "label": "Chat Zusammenfassung", "type": "bool",
         "value": _resolve_bool_pref(game, "chat_use_summary", "", True), "group": "Chat Memory"},
        {
            "key": "chat_summary_chars",
            "label": "Chat Summary Chars",
            "type": "choice",
            "value": str(max(100, min(5000, int(game.get("chat_summary_chars") or 1500)))),
            "options": ["500", "1000", "1500", "2500", "4000"],
            "group": "Chat Memory",
        },
        {
            "key": "chat_summary_update_every_turns",
            "label": "Summary Update (Turns)",
            "type": "choice",
            "value": str(max(1, min(20, int(game.get("chat_summary_update_every_turns") or 3)))),
            "options": ["1", "2", "3", "5", "10"],
            "group": "Chat Memory",
        },
        # ── Worker/Backend (CMW-010) ──────────────────────────────────────────
        {"key": "chat_pass_memory_to_worker", "label": "Memory an Worker", "type": "bool",
         "value": _resolve_bool_pref(game, "chat_pass_memory_to_worker", "", True), "group": "Chat Backend"},
        {
            "key": "chat_worker_mode",
            "label": "Worker Modus",
            "type": "choice",
            "value": str(game.get("chat_worker_mode") or "snake_ask"),
            "options": ["snake_ask", "propose", "auto"],
            "group": "Chat Backend",
        },
        {
            "key": "chat_backend_fallback",
            "label": "Chat Fallback",
            "type": "choice",
            "value": str(game.get("chat_backend_fallback") or "lmstudio"),
            "options": ["none", "lmstudio", "local_knowledge"],
            "group": "Chat Backend",
        },
        {"key": "chat_include_runtime_status", "label": "TUI-Status in Prompt", "type": "bool",
         "value": _resolve_bool_pref(game, "chat_include_runtime_status", "", False), "group": "Chat Memory"},
    ]


def refresh_chat_backend_models(game: dict[str, object], *, force: bool = False) -> tuple[list[str], str]:
    backend = str(game.get("chat_backend") or "ananta-worker").strip().lower()
    models_raw = game.get("chat_backend_models")
    models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
    models = [item for item in models if item and item != "-"]
    states_raw = game.get("chat_backend_model_states")
    model_states = dict(states_raw) if isinstance(states_raw, dict) else {}
    for model in _default_models_for_backend(backend, game):
        _append_unique(models, model)
        _set_model_state(model_states, model, None)

    local_backends = {"lmstudio", "local", "openai"}
    worker_backends = {"ananta-worker", "worker", "hub", "default", "auto", "opencode", "hermes"}
    if backend not in local_backends and backend not in worker_backends:
        game["chat_backend_models"] = models[-40:]
        game["chat_backend_models_error"] = ""
        return models[-40:], ""

    now = time.monotonic()
    last_refresh_raw = game.get("chat_backend_models_last_refresh_at")
    last_refresh = float(last_refresh_raw) if isinstance(last_refresh_raw, (int, float)) else 0.0
    if not force and (now - last_refresh) < 15.0:
        return models[-40:], str(game.get("chat_backend_models_error") or "")

    configured_base = str(game.get("chat_backend_api_base") or "").strip()
    worker_base = str(game.get("chat_worker_api_base") or "").strip()
    env_base = str(
        os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or ""
    ).strip()
    hub_base = str(os.environ.get("ANANTA_BASE_URL") or "").strip()
    base_candidates: list[str] = []
    lmstudio_candidates = _lmstudio_base_candidates()
    if backend in local_backends:
        candidates = (
            configured_base,
            env_base,
            *lmstudio_candidates,
            "http://localhost:1234",
            "http://127.0.0.1:1234",
        )
    else:
        candidates = (
            worker_base,
            hub_base,
            "http://localhost:5000",
            "http://127.0.0.1:5000",
            configured_base,
            *lmstudio_candidates,
        )
    for base in candidates:
        normalized = str(base or "").strip().rstrip("/")
        if normalized and normalized not in base_candidates:
            base_candidates.append(normalized)
    error = ""
    resolved_base = ""
    for base in base_candidates:
        endpoints = [f"{base}/models"]
        if not base.endswith("/v1"):
            endpoints.append(f"{base}/v1/models")
        for endpoint in endpoints:
            try:
                req = urllib.request.Request(endpoint, method="GET")
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                model_rows = data.get("data") if isinstance(data, dict) else []
                if isinstance(model_rows, list):
                    for row in model_rows:
                        if not isinstance(row, dict):
                            continue
                        model_id = _model_id_from_row(row)
                        if model_id:
                            _append_unique(models, model_id)
                            _set_model_state(model_states, model_id, True)
                resolved_base = base if base.endswith("/v1") else f"{base}/v1"
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                error = f"model-fetch fehlgeschlagen: {exc}"
        if resolved_base:
            break

    if backend in {"lmstudio", "local", "openai"}:
        lmstudio_extra_candidates: list[str] = []
        for base in base_candidates:
            host = base.removesuffix("/v1")
            endpoint = f"{host}/api/v0/models"
            if endpoint not in lmstudio_extra_candidates:
                lmstudio_extra_candidates.append(endpoint)
        for endpoint in lmstudio_extra_candidates:
            try:
                req = urllib.request.Request(endpoint, method="GET")
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                model_rows: list[Any] = []
                if isinstance(data, dict):
                    if isinstance(data.get("data"), list):
                        model_rows = list(data.get("data") or [])
                    elif isinstance(data.get("models"), list):
                        model_rows = list(data.get("models") or [])
                elif isinstance(data, list):
                    model_rows = data
                for row in model_rows:
                    if not isinstance(row, dict):
                        continue
                    model_id = _model_id_from_row(row)
                    if not model_id:
                        continue
                    _append_unique(models, model_id)
                    _set_model_state(model_states, model_id, _model_loaded_state_from_row(row))
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                continue

    models = models[-40:]
    game["chat_backend_models"] = models
    game["chat_backend_model_states"] = model_states
    game["chat_backend_models_last_refresh_at"] = now
    if resolved_base and backend in local_backends:
        game["chat_backend_api_base"] = resolved_base
        game["chat_backend_models_error"] = ""
        error = ""
    elif resolved_base and backend in worker_backends:
        game["chat_worker_api_base"] = resolved_base.removesuffix("/v1")
        game["chat_backend_models_error"] = ""
        error = ""
    else:
        game["chat_backend_models_error"] = error
    return models, error


def ai_snake_config_options(game: dict[str, object], *, key: str) -> list[str]:
    items = ai_snake_config_items(game)
    row = next((item for item in items if str(item.get("key") or "") == key), None)
    if row is None:
        return []
    typ = str(row.get("type") or "")
    if typ == "bool":
        return ["AN", "AUS"]
    options = [str(item).strip() for item in (row.get("options") or []) if str(item).strip()]
    value = str(game.get("chat_backend_model") if key == "chat_model" else row.get("value") or "").strip()
    if value and value not in options:
        options.insert(0, value)
    return options


def ai_snake_config_filter_options(game: dict[str, object], *, key: str, regex_filter: str) -> tuple[list[str], str]:
    options = ai_snake_config_options(game, key=key)
    pattern = str(regex_filter or "")
    if not pattern:
        return options, ""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return options, f"regex fehler: {exc}"
    filtered = [opt for opt in options if rx.search(opt)]
    return filtered, ""


def _parse_bool_value(raw: str) -> bool | None:
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "t", "yes", "y", "on", "an", "ja"}:
        return True
    if token in {"0", "false", "f", "no", "n", "off", "aus", "nein"}:
        return False
    return None


def apply_ai_snake_config_value(game: dict[str, object], *, key: str, value: str) -> str:
    items = ai_snake_config_items(game)
    row = next((item for item in items if str(item.get("key") or "") == key), None)
    if row is None:
        return "ai config: unbekanntes feld"
    label = str(row.get("label") or key)
    typ = str(row.get("type") or "")
    raw_value = str(value or "").strip()
    if not raw_value:
        return f"ai config: {label} leer"

    if typ == "bool":
        parsed = _parse_bool_value(raw_value)
        if parsed is None:
            return f"ai config: {label} erwartet AN/AUS"
        if key == "visual_enabled":
            game["tutorial_mode"] = parsed
        elif key == "chat_panel_open":
            game["chat_panel_open"] = parsed
        elif key == "visual_codecompass":
            game["ai_visual_use_codecompass"] = parsed
        elif key == "chat_use_codecompass":
            game["chat_use_codecompass"] = parsed
        elif key == "chat_include_local_project":
            game["chat_include_local_project"] = parsed
        elif key == "chat_include_wikipedia":
            game["chat_include_wikipedia"] = parsed
        elif key in {"chat_use_history", "chat_use_summary", "chat_pass_memory_to_worker", "chat_include_runtime_status"}:
            game[key] = parsed
        if key in {
            "visual_enabled", "chat_panel_open", "visual_codecompass", "chat_use_codecompass",
            "chat_include_local_project", "chat_include_wikipedia",
            "chat_use_history", "chat_use_summary", "chat_pass_memory_to_worker", "chat_include_runtime_status",
        }:
            _persist_tui_chat_settings(game)
        return f"ai config: {label} {'AN' if parsed else 'AUS'}"

    if key == "visual_provider":
        game["ai_snake_provider_preference"] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_backend":
        game["chat_backend"] = raw_value
        game["chat_backend_models_last_refresh_at"] = 0.0
        models, fetch_error = refresh_chat_backend_models(game, force=True)
        current_model = str(game.get("chat_backend_model") or "").strip()
        if models and (not current_model or current_model == "-"):
            game["chat_backend_model"] = models[0]
        _persist_tui_chat_settings(game)
        if models:
            return f"ai config: {label} -> {raw_value} ({len(models)} modelle)"
        if fetch_error:
            return f"ai config: {label} -> {raw_value} ({fetch_error})"
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_model":
        game["chat_backend_model"] = raw_value
        models_raw = game.get("chat_backend_models")
        models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
        if raw_value not in models:
            models.append(raw_value)
        game["chat_backend_models"] = [m for m in models if m][-40:]
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_api_base":
        game["chat_backend_api_base"] = raw_value.rstrip("/")
        game["chat_backend_models_last_refresh_at"] = 0.0
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {game['chat_backend_api_base']}"
    if key == "chat_ask_timeout_s":
        try:
            timeout_s = float(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet sekunden"
        timeout_s = max(3.0, min(180.0, timeout_s))
        game["chat_ask_timeout_s"] = timeout_s
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {timeout_s:g}s"
    if key == "chat_source_pack_id":
        game["chat_source_pack_id"] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_context_chars":
        try:
            value_int = int(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        value_int = max(500, min(20000, value_int))
        game["chat_context_chars"] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"
    if key == "chat_max_tokens":
        try:
            value_int = int(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        value_int = max(100, min(8000, value_int))
        game["chat_max_tokens"] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"
    if key == "chat_rag_top_k":
        try:
            value_int = int(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        value_int = max(8, min(120, value_int))
        game["chat_rag_top_k"] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"
    if key == "chat_answer_chars":
        try:
            value_int = int(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        value_int = max(600, min(12000, value_int))
        game["chat_answer_chars"] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"

    if key == "chat_history_turns":
        try:
            value_int = max(1, min(30, int(raw_value)))
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        game[key] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"

    if key == "chat_history_chars":
        try:
            value_int = max(100, min(10000, int(raw_value)))
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        game[key] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"

    if key == "chat_summary_chars":
        try:
            value_int = max(100, min(5000, int(raw_value)))
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        game[key] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"

    if key == "chat_summary_update_every_turns":
        try:
            value_int = max(1, min(20, int(raw_value)))
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        game[key] = value_int
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {value_int}"

    if key == "chat_worker_mode":
        if raw_value not in {"snake_ask", "propose", "auto"}:
            return f"ai config: {label} erwartet snake_ask/propose/auto"
        game[key] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"

    if key == "chat_backend_fallback":
        if raw_value not in {"none", "lmstudio", "local_knowledge"}:
            return f"ai config: {label} erwartet none/lmstudio/local_knowledge"
        game[key] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"

    return "ai config: keine änderung"


def apply_ai_snake_config_change(game: dict[str, object], *, key: str) -> str:
    options = ai_snake_config_options(game, key=key)
    if not options:
        return "ai config: keine optionen"
    items = ai_snake_config_items(game)
    row = next((item for item in items if str(item.get("key") or "") == key), None)
    current = str((row or {}).get("value") or "").strip()
    try:
        idx = options.index(current)
    except ValueError:
        idx = -1
    return apply_ai_snake_config_value(game, key=key, value=options[(idx + 1) % len(options)])
