"""AI-Snake chat provider resolution (legacy config plus central model route)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import current_app, has_app_context

from agent.config import settings
from agent.services.openai_credential_endpoint_binding import (
    OpenAICredentialEndpointBindingError,
    bind_openai_credential_endpoint,
)


def _resolve_central_ai_snake_model(config: dict[str, Any]):
    """Resolve an explicit central assignment; otherwise preserve legacy config."""

    if not has_app_context():
        return None
    from agent.services.model_runtime_selection_service import (
        ModelRuntimeSelectionError,
        resolve_explicit_hub_model,
    )
    from ananta_contracts.model_selection import ModelRoutingDryRunCommand

    configured_backend = str(config.get("chat_backend") or "").strip().lower()
    try:
        return resolve_explicit_hub_model(ModelRoutingDryRunCommand(
            consumer_id="chat.ai_snake",
            requires_streaming=True,
            allow_cloud=configured_backend in {"openai", "codex", "openrouter"},
        ))
    except ModelRuntimeSelectionError:
        raise
    except Exception as exc:
        current_app.logger.warning(
            "Central AI-Snake model route unavailable; using legacy fallback: %s",
            type(exc).__name__,
        )
        return None


def _resolve_ai_snake_chat_provider(
    config: dict[str, Any] | None = None,
    *,
    central_model_resolver: Callable[[dict[str, Any]], Any] = _resolve_central_ai_snake_model,
) -> tuple[str, str | None, str | None]:
    """Resolve ``(provider, model, api_base)``; an explicit central route wins.

    ``central_model_resolver`` looks up the central model assignment for the
    effective config (production: :func:`_resolve_central_ai_snake_model`).
    """
    provider = "lmstudio"
    model: str | None = None
    api_base: str | None = None
    cfg: dict[str, Any] = {}
    try:
        from agent.routes.ai_snake_config import _current_config

        cfg = dict(config) if config is not None else _current_config()
        configured_backend = str(cfg.get("chat_backend") or "").strip().lower()
        configured_model = str(cfg.get("chat_backend_model") or "").strip() or None
        configured_api_base = str(cfg.get("chat_backend_api_base") or "").strip() or None
        if configured_model:
            model = configured_model

        _openai_models = ("gpt-4", "gpt-3.5", "gpt-4o", "o1", "o3")
        is_openai_model = any(model.startswith(m) for m in _openai_models) if model else False
        is_openai_url = configured_api_base and "openai.com" in configured_api_base.lower()

        def _chat_completions_url(base_url: str) -> str:
            normalized = base_url.rstrip("/")
            return normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"

        if configured_backend in {"openai", "codex"} or (
            not configured_backend and (is_openai_url or is_openai_model)
        ):
            provider = "openai"
            api_base = bind_openai_credential_endpoint(
                client_api_base=configured_api_base,
                trusted_api_url=str(settings.openai_url),
                credential_ref=str(cfg.get("chat_backend_credential_ref") or ""),
            ).chat_completions_url
        elif configured_backend in {"ollama", "lmstudio"}:
            provider = configured_backend
            if configured_api_base:
                api_base = _chat_completions_url(configured_api_base)
    except OpenAICredentialEndpointBindingError:
        raise
    except Exception:
        pass

    central = central_model_resolver(cfg)
    if central is not None:
        provider, model = central.provider_id, central.model_id
        configured_api_base = central.base_url
        if provider == "openai":
            api_base = bind_openai_credential_endpoint(
                client_api_base=configured_api_base,
                trusted_api_url=str(settings.openai_url),
                credential_ref=str(cfg.get("chat_backend_credential_ref") or ""),
            ).chat_completions_url
        elif configured_api_base:
            normalized = configured_api_base.rstrip("/")
            api_base = (
                normalized
                if normalized.endswith("/chat/completions")
                else f"{normalized}/chat/completions"
            )
    return provider, model, api_base
