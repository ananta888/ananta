from __future__ import annotations

from flask import Flask

from agent.sdk import get_sdk

from .provider import AntiSlopEvolutionProvider


def init_app(app: Flask) -> None:
    cfg = (get_sdk(app).get_config("text_quality") or {}).get("evolution_provider", {})
    if not bool(cfg.get("enabled", False)):
        return
    get_sdk(app).register_evolution_provider(
        AntiSlopEvolutionProvider(),
        default=bool(cfg.get("default", False)),
        replace=False,
    )


__all__ = ["AntiSlopEvolutionProvider", "init_app"]
