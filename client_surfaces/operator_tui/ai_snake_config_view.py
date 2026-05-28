from __future__ import annotations

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


def apply_ai_snake_config_change(game: dict[str, object], *, key: str) -> str:
    items = ai_snake_config_items(game)
    row = next((item for item in items if str(item.get("key") or "") == key), None)
    if row is None:
        return "ai config: unbekanntes feld"
    typ = str(row.get("type") or "")
    label = str(row.get("label") or key)
    if typ == "bool":
        current = bool(row.get("value"))
        next_value = not current
        if key == "visual_enabled":
            game["tutorial_mode"] = next_value
        elif key == "chat_panel_open":
            game["chat_panel_open"] = next_value
        elif key == "visual_codecompass":
            game["ai_visual_use_codecompass"] = next_value
        return f"ai config: {label} {'AN' if next_value else 'AUS'}"

    if typ == "choice":
        options = [str(item).strip() for item in (row.get("options") or []) if str(item).strip()]
        if not options:
            return f"ai config: {label} keine optionen"
        current = str(row.get("value") or "").strip()
        try:
            idx = options.index(current)
        except ValueError:
            idx = -1
        next_value = options[(idx + 1) % len(options)]
        if key == "visual_provider":
            game["ai_snake_provider_preference"] = next_value
        elif key == "chat_backend":
            game["chat_backend"] = next_value
        elif key == "chat_model":
            game["chat_backend_model"] = next_value
            models_raw = game.get("chat_backend_models")
            models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
            if next_value not in models:
                models.append(next_value)
            game["chat_backend_models"] = [m for m in models if m][-40:]
        return f"ai config: {label} -> {next_value}"

    return "ai config: keine änderung"
