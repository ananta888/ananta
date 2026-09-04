"""Content-free collaboration health projection with bounded cardinality."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_workspace_store import CollaborationWorkspaceStore
from ananta_contracts.collaboration_workspace import canonical_digest


class CollaborationObservabilityService:
    """Evaluates aggregate workspace signals without exposing actor or content labels."""

    DEFAULT_THRESHOLDS = {
        "outbox_pending": 100,
        "outbox_retry": 10,
        "projection_lag": 50,
        "search_lag": 100,
    }

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        thresholds: Mapping[str, int] | None = None,
    ) -> None:
        selected = dict(thresholds or self.DEFAULT_THRESHOLDS)
        if set(selected) != set(self.DEFAULT_THRESHOLDS) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in selected.values()
        ):
            raise ValueError("collaboration_observability_thresholds_invalid")
        self._store = store
        self._thresholds = selected

    def snapshot(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        metrics = self._store.operational_snapshot(tenant_id, workspace_id)
        alerts = [
            {
                "signal": signal,
                "reason_code": f"collaboration_{signal}_slo_exceeded",
                "observed": metrics[signal],
                "threshold": threshold,
            }
            for signal, threshold in sorted(self._thresholds.items())
            if metrics[signal] > threshold
        ]
        return {
            "schema": "ananta.collaboration-observability.v1",
            "scope": "workspace",
            "metrics": metrics,
            "alerts": alerts,
            "healthy": not alerts,
            "threshold_digest": canonical_digest(self._thresholds),
            "excluded_dimensions": [
                "actor_id",
                "room_id",
                "event_id",
                "payload",
                "prompt",
                "secret",
                "key",
                "nonce",
                "message_text",
            ],
        }


__all__ = ["CollaborationObservabilityService"]
