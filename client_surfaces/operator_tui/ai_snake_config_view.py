from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


def ai_snake_config_items(game: dict[str, object]) -> list[dict[str, object]]:
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
    timeout_value_raw = game.get("chat_ask_timeout_s")
    try:
        timeout_value = float(timeout_value_raw) if timeout_value_raw is not None else 45.0
    except (TypeError, ValueError):
        timeout_value = 45.0
    timeout_value = max(3.0, min(180.0, timeout_value))
    timeout_options = ["20", "30", "45", "60", "90", "120", "180"]
    return [
        {"key": "visual_enabled", "label": "Visual AI-Snake", "type": "bool", "value": visual_on},
        {"key": "chat_panel_open", "label": "AI-Chat Panel", "type": "bool", "value": chat_open},
        {"key": "visual_provider", "label": "Visual Provider", "type": "choice", "value": visual_provider, "options": visual_providers},
        {"key": "visual_codecompass", "label": "Visual CodeCompass", "type": "bool", "value": codecompass_on},
        {
            "key": "chat_backend",
            "label": "Chat Backend",
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
            "key": "chat_ask_timeout_s",
            "label": "Chat Ask Timeout (s)",
            "type": "choice",
            "value": f"{timeout_value:g}",
            "options": timeout_options,
        },
    ]


def refresh_chat_backend_models(game: dict[str, object], *, force: bool = False) -> tuple[list[str], str]:
    backend = str(game.get("chat_backend") or "ananta-worker").strip().lower()
    models_raw = game.get("chat_backend_models")
    models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
    models = [item for item in models if item]
    if backend not in {"lmstudio", "local", "openai"}:
        game["chat_backend_models"] = models[-40:]
        game["chat_backend_models_error"] = ""
        return models[-40:], ""

    now = time.monotonic()
    last_refresh_raw = game.get("chat_backend_models_last_refresh_at")
    last_refresh = float(last_refresh_raw) if isinstance(last_refresh_raw, (int, float)) else 0.0
    if not force and (now - last_refresh) < 15.0:
        return models[-40:], str(game.get("chat_backend_models_error") or "")

    configured_base = str(game.get("chat_backend_api_base") or "").strip()
    env_base = str(
        os.environ.get("ANANTA_TUI_CHAT_API_BASE_URL")
        or os.environ.get("ANANTA_TUI_SNAKE_AI_API_BASE_URL")
        or ""
    ).strip()
    base_candidates: list[str] = []
    for base in (
        configured_base,
        env_base,
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://localhost:1234",
        "http://127.0.0.1:1234",
    ):
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
                        model_id = str(row.get("id") or "").strip()
                        if model_id and model_id not in models:
                            models.append(model_id)
                resolved_base = base if base.endswith("/v1") else f"{base}/v1"
                break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                error = f"model-fetch fehlgeschlagen: {exc}"
        if resolved_base:
            break

    models = models[-40:]
    game["chat_backend_models"] = models
    game["chat_backend_models_last_refresh_at"] = now
    if resolved_base:
        game["chat_backend_api_base"] = resolved_base
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
    value = str(row.get("value") or "").strip()
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
        return f"ai config: {label} {'AN' if parsed else 'AUS'}"

    if key == "visual_provider":
        game["ai_snake_provider_preference"] = raw_value
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_backend":
        game["chat_backend"] = raw_value
        game["chat_backend_models_last_refresh_at"] = 0.0
        if raw_value.strip().lower() in {"lmstudio", "local", "openai"}:
            models, fetch_error = refresh_chat_backend_models(game, force=True)
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
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_ask_timeout_s":
        try:
            timeout_s = float(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet sekunden"
        timeout_s = max(3.0, min(180.0, timeout_s))
        game["chat_ask_timeout_s"] = timeout_s
        return f"ai config: {label} -> {timeout_s:g}s"
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
