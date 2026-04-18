from __future__ import annotations

from flask import Flask
from urllib.parse import urlparse

from agent.sdk import get_sdk

from .adapter import EvolverAdapter


def init_app(app: Flask) -> None:
    sdk = get_sdk(app)
    evolution_cfg = sdk.get_config("evolution")
    provider_cfg = dict((evolution_cfg.get("provider_overrides") or {}).get("evolver") or {})

    enabled = bool(provider_cfg.get("enabled", False))
    if not enabled:
        return
    _validate_provider_policy(provider_cfg)

    sdk.register_evolution_provider(
        EvolverAdapter.from_config(provider_cfg),
        default=bool(provider_cfg.get("default", False)),
        replace=bool(provider_cfg.get("replace", True)),
    )


def _validate_provider_policy(provider_cfg: dict) -> None:
    allowed_hosts = provider_cfg.get("allowed_hosts") or []
    if isinstance(allowed_hosts, str):
        allowed_hosts = [item.strip() for item in allowed_hosts.split(",") if item.strip()]
    if allowed_hosts:
        parsed = urlparse(str(provider_cfg.get("base_url") or ""))
        if parsed.hostname not in set(allowed_hosts):
            raise ValueError("evolver_base_url_host_not_allowed")
    if bool(provider_cfg.get("force_analyze_only", True)):
        provider_cfg["validate_allowed"] = False
        provider_cfg["apply_allowed"] = False


__all__ = ["EvolverAdapter", "init_app"]
