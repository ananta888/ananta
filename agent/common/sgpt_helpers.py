from __future__ import annotations

import logging
import os

from flask import current_app, has_app_context

from agent.config import settings


def _get_agent_config() -> dict:
    if has_app_context():
        return (current_app.config.get("AGENT_CONFIG", {}) or {})
    return {}


def _get_runtime_provider_urls() -> dict:
    defaults = {
        "ollama": getattr(settings, "ollama_url", None),
        "lmstudio": getattr(settings, "lmstudio_url", None),
        "openai": getattr(settings, "openai_url", None),
        "anthropic": getattr(settings, "anthropic_url", None),
        "mock": getattr(settings, "mock_url", None),
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


def _get_runtime_default_provider() -> str:
    agent_cfg = _get_agent_config()
    return str(agent_cfg.get("default_provider") or settings.default_provider or "").strip().lower()


def _resolve_profile_api_key(profile_name: str | None) -> str | None:
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


def _resolve_openai_compatible_base_url() -> str | None:
    from agent.llm_integration import _normalize_lmstudio_base_url

    provider = _get_runtime_default_provider()
    provider_urls = _get_runtime_provider_urls()
    if provider == "lmstudio":
        return _normalize_lmstudio_base_url(provider_urls.get("lmstudio") or settings.lmstudio_url)
    elif provider in {"openai", "codex"}:
        raw_url = provider_urls.get("openai") or provider_urls.get("codex") or settings.openai_url
    else:
        raw_url = (
            provider_urls.get("openai")
            or provider_urls.get("codex")
            or provider_urls.get("lmstudio")
            or settings.openai_url
            or settings.lmstudio_url
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
    if any(marker in raw for marker in ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        return "private_network"
    if raw.startswith("http://") or raw.startswith("https://"):
        return "remote"
    return "unknown"
