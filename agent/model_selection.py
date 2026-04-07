from __future__ import annotations

from typing import Final

_LEGACY_OLLAMA_MODEL_ALIASES: Final[dict[str, str]] = {
    "ananta-default": "qwen2.5-coder:7b",
    "ananta-default:latest": "qwen2.5-coder:7b",
    "opencode/glm-5-free": "qwen2.5-coder:7b",
}

_MODEL_PROVIDER_PREFIXES: Final[set[str]] = {
    "ollama",
    "lmstudio",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "copilot",
    "codex",
}


def normalize_legacy_model_name(model: str | None, *, provider: str | None = None) -> str | None:
    normalized = str(model or "").strip()
    if not normalized:
        return None

    explicit_provider = None
    bare_model = normalized
    if "/" in normalized:
        maybe_provider, maybe_model = normalized.split("/", 1)
        if maybe_provider.strip().lower() in _MODEL_PROVIDER_PREFIXES and maybe_model.strip():
            explicit_provider = maybe_provider.strip().lower()
            bare_model = maybe_model.strip()

    effective_provider = str(provider or explicit_provider or "").strip().lower()
    if effective_provider != "ollama":
        return normalized

    replacement = _LEGACY_OLLAMA_MODEL_ALIASES.get(bare_model.strip().lower())
    if not replacement:
        return normalized
    if explicit_provider:
        return f"{explicit_provider}/{replacement}"
    return replacement
