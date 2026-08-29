"""Bounded, complaint-resistant relay health and failover policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ananta_contracts.peer_overlay import PeerRouteLease, require_overlay_id


@dataclass(frozen=True, slots=True)
class RelayDeliveryObservation:
    observer_peer_id: str
    relay_peer_id: str
    route_epoch: int
    delivery_ratio: float
    delay_ms: int
    sample_count: int
    observed_at_ms: int

    def __post_init__(self) -> None:
        require_overlay_id(self.observer_peer_id, "observer_peer_id")
        require_overlay_id(self.relay_peer_id, "relay_peer_id")
        if self.route_epoch < 1 or not 0 <= self.delivery_ratio <= 1:
            raise ValueError("peer_overlay_observation_value_invalid")
        if not 0 <= self.delay_ms <= 120_000 or not 1 <= self.sample_count <= 10_000:
            raise ValueError("peer_overlay_observation_value_invalid")
        if self.observed_at_ms < 1:
            raise ValueError("peer_overlay_observation_time_invalid")


class PeerOverlayRelayHealthPolicy:
    def __init__(self, *, window_ms: int = 15_000, cooldown_ms: int = 30_000) -> None:
        if window_ms < 1_000 or cooldown_ms < window_ms:
            raise ValueError("peer_overlay_health_budget_invalid")
        self._window_ms = window_ms
        self._cooldown_ms = cooldown_ms

    def evaluate(
        self,
        *,
        lease: PeerRouteLease,
        observations: Iterable[RelayDeliveryObservation | Mapping[str, Any]],
        now_ms: int,
        last_failover_at_ms: int | None,
    ) -> dict[str, Any]:
        if lease.backup_parent_id is None:
            return _decision(False, "peer_overlay_backup_parent_unavailable", 0, ())
        if last_failover_at_ms is not None and now_ms - last_failover_at_ms < self._cooldown_ms:
            return _decision(
                False,
                "peer_overlay_failover_cooldown",
                self._cooldown_ms - (now_ms - last_failover_at_ms),
                (),
            )
        latest: dict[str, RelayDeliveryObservation] = {}
        for raw in observations:
            value = raw if isinstance(raw, RelayDeliveryObservation) else RelayDeliveryObservation(**dict(raw))
            if (
                value.relay_peer_id != lease.primary_parent_id
                or value.route_epoch != lease.epochs.route
                or value.observed_at_ms > now_ms
                or now_ms - value.observed_at_ms > self._window_ms
            ):
                continue
            current = latest.get(value.observer_peer_id)
            if current is None or value.observed_at_ms > current.observed_at_ms:
                latest[value.observer_peer_id] = value
        complaints = tuple(
            sorted(
                observer
                for observer, value in latest.items()
                if value.sample_count >= 5 and (value.delivery_ratio < 0.8 or value.delay_ms > 3_000)
            )
        )
        quorum = len(complaints) >= 2 and len(complaints) * 2 >= len(latest)
        return _decision(
            quorum,
            "peer_overlay_failover_quorum_reached" if quorum else "peer_overlay_failover_quorum_missing",
            0,
            complaints,
        )


def _decision(switch: bool, reason_code: str, retry_after_ms: int, complaints: tuple[str, ...]) -> dict[str, Any]:
    return {
        "switch_to_backup": switch,
        "reason_code": reason_code,
        "retry_after_ms": retry_after_ms,
        "complaint_count": len(complaints),
        "complaining_peer_ids": list(complaints),
        "permanent_double_traffic_allowed": False,
        "human_intervention_required": False,
    }


__all__ = ["PeerOverlayRelayHealthPolicy", "RelayDeliveryObservation"]
