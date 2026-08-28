"""Read-model projection for the Scrum continuous-improvement dashboard."""

from __future__ import annotations

from typing import Any

from agent.services.scrum_state_store import ScrumStateStorePort


class ScrumContinuousImprovementQueryService:
    def __init__(self, store: ScrumStateStorePort) -> None:
        self._store = store

    def overview(self, *, scope_id: str) -> dict[str, Any]:
        scope = str(scope_id or "").strip()
        if not scope or len(scope) > 256:
            raise ValueError("scrum_scope_id_invalid")
        sprints = self._store.list("sprint", scope_id=scope)
        baselines = self._store.list("architecture_baseline", scope_id=scope)
        commitments = self._store.list("improvement_commitment", scope_id=scope)
        improvement_effects = self._store.list("improvement_effect", scope_id=scope)
        architecture_effects = self._store.list("architecture_effect", scope_id=scope)
        return {
            "schema": "ananta.scrum-continuous-improvement-overview.v1",
            "scope_id": scope,
            "sprints": sorted(sprints, key=lambda item: int(item["sequence"])),
            "architecture_baselines": baselines,
            "improvement_commitments": commitments,
            "improvement_effects": improvement_effects,
            "architecture_effects": architecture_effects,
            "counts": {
                "sprints": len(sprints),
                "active_architecture_baselines": sum(
                    item["lifecycle_state"] == "active" for item in baselines
                ),
                "accepted_commitments": sum(item["status"] == "accepted" for item in commitments),
                "rolled_back_commitments": sum(item["status"] == "rolled_back" for item in commitments),
            },
        }


__all__ = ["ScrumContinuousImprovementQueryService"]
