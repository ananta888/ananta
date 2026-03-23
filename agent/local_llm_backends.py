from __future__ import annotations

from typing import Any

from agent.llm_integration import _list_lmstudio_candidates, _normalize_lmstudio_base_url, probe_lmstudio_runtime


def normalize_openai_compatible_base_url(url: str | None) -> str | None:
    return _normalize_lmstudio_base_url(url)


def _normalize_local_backend_entry(
    item: dict[str, Any] | None,
    *,
    default_provider: str | None = None,
    default_model: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    backend_id = str(item.get("id") or item.get("provider") or "").strip().lower()
    if not backend_id:
        return None
    base_url = normalize_openai_compatible_base_url(item.get("base_url"))
    models = item.get("models") if isinstance(item.get("models"), list) else []
    tool_calling = item.get("supports_tool_calls")
    if tool_calling is None:
        tool_calling = item.get("tool_calling")
    return {
        "provider": backend_id,
        "transport_provider": "openai",
        "name": str(item.get("name") or backend_id),
        "base_url": base_url,
        "api_key": str(item.get("api_key") or "").strip() or None,
        "api_key_profile": str(item.get("api_key_profile") or "").strip() or None,
        "supports_tool_calls": bool(tool_calling),
        "configured_models": [str(x).strip() for x in models if str(x).strip()],
        "source": "agent_config.local_openai_backends",
        "selected": default_provider == backend_id,
        "selected_model": default_model if default_provider == backend_id else None,
    }


def get_local_openai_backends(
    *,
    agent_cfg: dict[str, Any] | None = None,
    provider_urls: dict[str, Any] | None = None,
    default_provider: str | None = None,
    default_model: str | None = None,
) -> list[dict[str, Any]]:
    agent_cfg = agent_cfg or {}
    provider_urls = provider_urls or {}
    entries: list[dict[str, Any]] = []

    lmstudio_url = normalize_openai_compatible_base_url(provider_urls.get("lmstudio") or agent_cfg.get("lmstudio_url"))
    entries.append(
        {
            "provider": "lmstudio",
            "transport_provider": "openai",
            "name": "LM Studio",
            "base_url": lmstudio_url,
            "api_key": "sk-no-key-needed" if lmstudio_url else None,
            "api_key_profile": None,
            "supports_tool_calls": True,
            "configured_models": [],
            "source": "provider_urls.lmstudio" if provider_urls.get("lmstudio") else "agent_config.lmstudio_url",
            "selected": default_provider == "lmstudio",
            "selected_model": default_model if default_provider == "lmstudio" else None,
        }
    )

    for raw_item in agent_cfg.get("local_openai_backends") or []:
        normalized = _normalize_local_backend_entry(
            raw_item,
            default_provider=default_provider,
            default_model=default_model,
        )
        if normalized:
            entries.append(normalized)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        provider = str(entry.get("provider") or "").strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        deduped.append(entry)
    return deduped


def resolve_local_openai_backend(
    provider: str | None,
    *,
    agent_cfg: dict[str, Any] | None = None,
    provider_urls: dict[str, Any] | None = None,
    default_provider: str | None = None,
    default_model: str | None = None,
) -> dict[str, Any] | None:
    wanted = str(provider or "").strip().lower()
    if not wanted:
        return None
    for entry in get_local_openai_backends(
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
        default_provider=default_provider,
        default_model=default_model,
    ):
        if entry["provider"] == wanted:
            return entry
    return None


def list_openai_compatible_models(base_url: str | None, timeout: int) -> list[dict[str, Any]]:
    normalized = normalize_openai_compatible_base_url(base_url)
    if not normalized:
        return []
    return _list_lmstudio_candidates(normalized, timeout=timeout)


def probe_openai_compatible_backend(base_url: str | None, timeout: int) -> dict[str, Any]:
    normalized = normalize_openai_compatible_base_url(base_url)
    if not normalized:
        return {
            "ok": False,
            "status": "invalid_url",
            "base_url": base_url,
            "models_url": None,
            "candidates": [],
            "candidate_count": 0,
        }
    return probe_lmstudio_runtime(normalized, timeout=timeout)
