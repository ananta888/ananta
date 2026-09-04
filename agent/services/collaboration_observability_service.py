"""Content-free collaboration health projection with bounded cardinality."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.collaboration_operational_signals import CollaborationOperationalSignals
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
    DEFAULT_SIGNAL_THRESHOLDS = {
        "errors": 5,
        "slow_operations": 5,
        "revocation_errors": 0,
        "reconnect_errors": 3,
        "loop_detections": 3,
    }

    def __init__(
        self,
        store: CollaborationWorkspaceStore,
        *,
        thresholds: Mapping[str, int] | None = None,
        signals: CollaborationOperationalSignals | None = None,
        signal_thresholds: Mapping[str, int] | None = None,
    ) -> None:
        selected = dict(thresholds or self.DEFAULT_THRESHOLDS)
        if set(selected) != set(self.DEFAULT_THRESHOLDS) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in selected.values()
        ):
            raise ValueError("collaboration_observability_thresholds_invalid")
        self._store = store
        self._thresholds = selected
        selected_signal_thresholds = dict(signal_thresholds or self.DEFAULT_SIGNAL_THRESHOLDS)
        if set(selected_signal_thresholds) != set(self.DEFAULT_SIGNAL_THRESHOLDS) or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in selected_signal_thresholds.values()
        ):
            raise ValueError("collaboration_observability_signal_thresholds_invalid")
        self._signals = signals or CollaborationOperationalSignals()
        self._signal_thresholds = selected_signal_thresholds

    def record(self, signal: str, *, outcome: str, duration_ms: float = 0.0) -> None:
        self._signals.record(signal, outcome=outcome, duration_ms=duration_ms)

    def snapshot(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        metrics = self._store.operational_snapshot(tenant_id, workspace_id)
        signal_metrics = self._signals.snapshot()
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
        alerts.extend(
            {
                "signal": signal,
                "reason_code": f"collaboration_{signal}_slo_exceeded",
                "observed": signal_metrics[signal],
                "threshold": threshold,
            }
            for signal, threshold in sorted(self._signal_thresholds.items())
            if signal_metrics[signal] > threshold
        )
        return {
            "schema": "ananta.collaboration-observability.v1",
            "scope": "workspace",
            "metrics": metrics,
            "runtime_signals": signal_metrics,
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
