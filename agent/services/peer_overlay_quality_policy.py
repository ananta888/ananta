"""Content-free, quorum-based Hub aggregation for peer-overlay quality."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Mapping

from ananta_contracts.peer_overlay import require_overlay_id


@dataclass(frozen=True, slots=True)
class AcceptedPeerQualityObservation:
    validation: str
    observer_peer_id: str
    relay_peer_id: str
    route_epoch: int
    observed_at_ms: int
    sample_count: int
    link_state: str
    relay_delivery_ratio: float
    end_to_end_delay_ms: int

    def __post_init__(self) -> None:
        if self.validation != "hub-quality-observation-accepted-v1":
            raise ValueError("peer_quality_observation_unverified")
        require_overlay_id(self.observer_peer_id, "observer_peer_id")
        require_overlay_id(self.relay_peer_id, "relay_peer_id")
        if self.link_state not in {"good", "degraded", "failed"}:
            raise ValueError("peer_quality_link_state_invalid")
        if type(self.route_epoch) is not int or self.route_epoch < 1:
            raise ValueError("peer_quality_route_epoch_invalid")
        if type(self.observed_at_ms) is not int or self.observed_at_ms < 1:
            raise ValueError("peer_quality_observed_at_invalid")
        if type(self.sample_count) is not int or not 1 <= self.sample_count <= 10_000:
            raise ValueError("peer_quality_sample_count_invalid")
        if not 0.0 <= self.relay_delivery_ratio <= 1.0:
            raise ValueError("peer_quality_delivery_ratio_invalid")
        if type(self.end_to_end_delay_ms) is not int or not 0 <= self.end_to_end_delay_ms <= 120_000:
            raise ValueError("peer_quality_delay_invalid")


class PeerOverlayQualityPolicy:
    """Produces an ephemeral coarse decision; it owns no behavioral history."""

    def __init__(self, *, window_ms: int = 15_000, minimum_quorum: int = 2, maximum_observations: int = 256) -> None:
        if not 1_000 <= window_ms <= 120_000 or not 2 <= minimum_quorum <= 16:
            raise ValueError("peer_quality_policy_budget_invalid")
        if not minimum_quorum <= maximum_observations <= 1_024:
            raise ValueError("peer_quality_policy_budget_invalid")
        self._window_ms = window_ms
        self._minimum_quorum = minimum_quorum
        self._maximum_observations = maximum_observations

    def aggregate(
        self,
        *,
        relay_peer_id: str,
        route_epoch: int,
        observations: Iterable[AcceptedPeerQualityObservation | Mapping[str, Any]],
        now_ms: int,
    ) -> dict[str, Any]:
        relay = require_overlay_id(relay_peer_id, "relay_peer_id")
        values = list(observations)
        if len(values) > self._maximum_observations:
            raise ValueError("peer_quality_observation_budget_exceeded")
        latest: dict[str, AcceptedPeerQualityObservation] = {}
        for raw in values:
            value = (
                raw
                if isinstance(raw, AcceptedPeerQualityObservation)
                else AcceptedPeerQualityObservation(**dict(raw))
            )
            if (
                value.relay_peer_id != relay
                or value.route_epoch != route_epoch
                or value.observed_at_ms > now_ms
                or now_ms - value.observed_at_ms > self._window_ms
                or value.sample_count < 5
            ):
                continue
            prior = latest.get(value.observer_peer_id)
            if prior is None or value.observed_at_ms > prior.observed_at_ms:
                latest[value.observer_peer_id] = value
        accepted = tuple(latest.values())
        if len(accepted) < self._minimum_quorum:
            return _result("insufficient_quorum", len(accepted), "unknown", "unknown", "unknown")
        quorum = len(accepted) // 2 + 1
        link_bad = sum(item.link_state != "good" for item in accepted)
        relay_bad = sum(item.relay_delivery_ratio < 0.9 for item in accepted)
        end_to_end_bad = sum(item.end_to_end_delay_ms > 1_000 for item in accepted)
        link = "degraded" if link_bad >= quorum else "good"
        relay_state = "degraded" if relay_bad >= quorum else "good"
        end_to_end = "degraded" if end_to_end_bad >= quorum else "good"
        reason = "peer_quality_degraded" if "degraded" in {link, relay_state, end_to_end} else "peer_quality_good"
        return {
            **_result(reason, len(accepted), link, relay_state, end_to_end),
            "delivery_bucket": _delivery_bucket(float(median(item.relay_delivery_ratio for item in accepted))),
            "delay_bucket": _delay_bucket(int(median(item.end_to_end_delay_ms for item in accepted))),
        }


def _result(reason: str, count: int, link: str, relay: str, end_to_end: str) -> dict[str, Any]:
    return {
        "reason_code": reason,
        "observer_count": count,
        "link_state": link,
        "relay_state": relay,
        "end_to_end_state": end_to_end,
        "contains_pii": False,
        "contains_network_address": False,
        "human_intervention_required": False,
    }


def _delivery_bucket(value: float) -> str:
    return "good" if value >= 0.98 else "degraded" if value >= 0.9 else "poor"


def _delay_bucket(value: int) -> str:
    return "low" if value <= 250 else "medium" if value <= 1_000 else "high"


__all__ = ["AcceptedPeerQualityObservation", "PeerOverlayQualityPolicy"]
