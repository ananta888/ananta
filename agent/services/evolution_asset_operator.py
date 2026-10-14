"""Small scoped projection seam; routes never access repositories directly."""

from __future__ import annotations


class EvolutionAssetOperator:
    def __init__(self, store):
        self._store = store

    def list(self, **scope):
        return self._store.list(**scope)


def get_evolution_asset_operator():
    from flask import current_app, has_app_context

    if not has_app_context() or current_app.config.get("ROLE") != "hub":
        raise ValueError("evolution_asset_hub_required")
    service = current_app.extensions.get("evolution_asset_operator")
    if service is None:
        from agent.repositories.evolution_assets import EvolutionAssetStore

        service = EvolutionAssetOperator(EvolutionAssetStore())
        current_app.extensions["evolution_asset_operator"] = service
    return service
