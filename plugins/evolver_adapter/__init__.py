from __future__ import annotations

from flask import Flask

from agent.services.evolution import register_evolution_provider

from .adapter import EvolverAdapter


def init_app(app: Flask) -> None:
    cfg = app.config.get("AGENT_CONFIG") or {}
    evolution_cfg = cfg.get("evolution") or {}
    provider_cfg = dict((evolution_cfg.get("provider_overrides") or {}).get("evolver") or {})
    enabled = bool(provider_cfg.get("enabled", False))
    if not enabled:
        return

    register_evolution_provider(
        EvolverAdapter.from_config(provider_cfg),
        app=app,
        default=bool(provider_cfg.get("default", False)),
        replace=bool(provider_cfg.get("replace", True)),
    )


__all__ = ["EvolverAdapter", "init_app"]
