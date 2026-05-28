from __future__ import annotations

import re
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
    ]


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
        return f"ai config: {label} -> {raw_value}"
    if key == "chat_model":
        game["chat_backend_model"] = raw_value
        models_raw = game.get("chat_backend_models")
        models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
        if raw_value not in models:
            models.append(raw_value)
        game["chat_backend_models"] = [m for m in models if m][-40:]
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
