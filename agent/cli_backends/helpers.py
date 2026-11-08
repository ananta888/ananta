from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import current_app, has_app_context

# Production default of every ``backend_settings=`` parameter below.
from agent.config import settings


def _get_agent_config() -> dict:
    if has_app_context():
        return current_app.config.get("AGENT_CONFIG", {}) or {}
    return {}


def _get_runtime_provider_urls(*, backend_settings: Any | None = None) -> dict:
    backend_settings = settings if backend_settings is None else backend_settings
    defaults = {
        "ollama": getattr(backend_settings, "ollama_url", None),
        "lmstudio": getattr(backend_settings, "lmstudio_url", None),
        "openai": getattr(backend_settings, "openai_url", None),
        "anthropic": getattr(backend_settings, "anthropic_url", None),
        "mock": getattr(backend_settings, "mock_url", None),
    }
    if not has_app_context():
        return defaults
    configured = current_app.config.get("PROVIDER_URLS", {}) or {}
    if not isinstance(configured, dict):
        return defaults
    return {
        **defaults,
        **{key: value for key, value in configured.items() if value},
    }


def _get_runtime_default_provider(*, backend_settings: Any | None = None) -> str:
    backend_settings = settings if backend_settings is None else backend_settings
    agent_cfg = _get_agent_config()
    return str(agent_cfg.get("default_provider") or backend_settings.default_provider or "").strip().lower()


# Local endpoints run without credentials; they keep this literal no-auth token.
_BEARER_PLACEHOLDER = "sk-no-key-needed"


def _resolve_profile_api_key(profile_name: str | None) -> str | None:
    """Resolve a named API-key profile to its secret.

    Profile entries are a literal secret string, or a dict with one of
    ``api_key`` (literal), ``env`` (environment variable name) or ``file``
    (path to a secret file). ``env``/``file`` keep the secret out of the
    editable configuration entirely.
    """
    profile_name = str(profile_name or "").strip()
    if not profile_name:
        return None
    agent_cfg = _get_agent_config()
    profiles = agent_cfg.get("llm_api_key_profiles") or {}
    if not isinstance(profiles, dict):
        return None
    selected = profiles.get(profile_name)
    if isinstance(selected, str):
        return selected.strip() or None
    if isinstance(selected, dict):
        env_name = str(selected.get("env") or "").strip()
        if env_name:
            return os.environ.get(env_name) or None
        file_path = str(selected.get("file") or "").strip()
        if file_path:
            try:
                return Path(file_path).read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
        return str(selected.get("api_key") or "").strip() or None
    return None


def _is_probably_local_base_url(url: str | None) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return False
    local_markers = (
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "192.168.",
        "10.",
        "172.16.",
        "172.17.",
        "172.18.",
        "172.19.",
        "172.20.",
        "172.21.",
        "172.22.",
        "172.23.",
        "172.24.",
        "172.25.",
        "172.26.",
        "172.27.",
        "172.28.",
        "172.29.",
        "172.30.",
        "172.31.",
    )
    return any(marker in raw for marker in local_markers)


def _normalize_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    normalized_lmstudio = _normalize_lmstudio_base_url(raw_url)
    if normalized_lmstudio:
        return normalized_lmstudio
    normalized = raw_url
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _normalize_ollama_openai_base_url(url: str | None) -> str | None:
    from agent.llm_integration import _normalize_ollama_base_url

    normalized = _normalize_ollama_base_url(url)
    if not normalized:
        return None
    normalized = normalized.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _resolve_openai_compatible_base_url(*, backend_settings: Any | None = None) -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    backend_settings = settings if backend_settings is None else backend_settings
    provider = _get_runtime_default_provider(backend_settings=backend_settings)
    provider_urls = _get_runtime_provider_urls(backend_settings=backend_settings)
    if provider == "lmstudio":
        return _normalize_lmstudio_base_url(provider_urls.get("lmstudio") or backend_settings.lmstudio_url)
    elif provider in {"openai", "codex"}:
        raw_url = provider_urls.get("openai") or provider_urls.get("codex") or backend_settings.openai_url
    else:
        raw_url = (
            provider_urls.get("openai")
            or provider_urls.get("codex")
            or provider_urls.get("lmstudio")
            or backend_settings.openai_url
            or backend_settings.lmstudio_url
        )

    if not raw_url:
        return None

    normalized = raw_url.strip()
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized.rstrip("/")


def _classify_runtime_target(url: str | None) -> str | None:
    raw = (url or "").strip().lower()
    if not raw:
        return None
    if "host.docker.internal" in raw:
        return "docker_host"
    if "localhost" in raw or "127.0.0.1" in raw:
        return "loopback"
    if any(
        marker in raw
        for marker in (
            "192.168.",
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
        )
    ):
        return "private_network"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "remote"
    return "unknown"


def _resolve_api_key(base_url: str | None) -> str:
    """Bearer token for the resolved endpoint.

    A registered local backend entry owns its credentials (``api_key`` or
    ``api_key_profile``); the shared OpenAI settings cover the default
    endpoint. Endpoints without credentials keep the local no-auth placeholder.
    """
    normalized = _normalize_openai_base_url(base_url)
    if not normalized:
        return _BEARER_PLACEHOLDER
    from agent.local_llm_backends import get_local_openai_backends

    for entry in get_local_openai_backends(
        agent_cfg=_get_agent_config(),
        provider_urls=_get_runtime_provider_urls(),
        default_provider=_get_runtime_default_provider(),
    ):
        if _normalize_openai_base_url(entry.get("base_url")) != normalized:
            continue
        key = str(entry.get("api_key") or "").strip()
        if not key and entry.get("api_key_profile"):
            key = (_resolve_profile_api_key(entry.get("api_key_profile")) or "").strip()
        if key:
            return key
        return _BEARER_PLACEHOLDER
    if _normalize_openai_base_url(getattr(settings, "openai_url", None)) == normalized:
        key = str(getattr(settings, "openai_api_key", "") or "").strip()
        if key:
            return key
    return _BEARER_PLACEHOLDER
