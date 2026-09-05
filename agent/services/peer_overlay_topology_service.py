"""Deterministic Hub policy for publication-scoped peer data DAGs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from ananta_contracts.peer_overlay import (
    OverlayCapability,
    OverlayEpochs,
    OverlayTrafficClass,
    PeerRouteLease,
    canonical_overlay_digest,
    require_overlay_id,
)


@dataclass(frozen=True, slots=True)
class PeerOverlayCandidate:
    peer_id: str
    relay_consent: bool
    visible: bool
    battery: str
    network: str
    self_capacity: int
    observed_capacity: int
    delivery_ratio: float
    turn_required: bool = False
    cpu_load_ratio: float = 1.0
    rtt_ms: int = 120_000
    packet_loss_ratio: float = 1.0
    send_buffer_bytes: int = 64 * 1024 * 1024
    metered_network: bool = True
    eligible_since_ms: int = 0

    def __post_init__(self) -> None:
        require_overlay_id(self.peer_id, "peer_id")
        if self.battery not in {"unknown", "critical", "limited", "mains"}:
            raise ValueError("peer_overlay_battery_class_invalid")
        if self.network not in {"unknown", "constrained", "normal", "fast"}:
            raise ValueError("peer_overlay_network_class_invalid")
        if any(not 0 <= value <= 100 for value in (self.self_capacity, self.observed_capacity)):
            raise ValueError("peer_overlay_capacity_invalid")
        if not 0.0 <= self.delivery_ratio <= 1.0:
            raise ValueError("peer_overlay_delivery_ratio_invalid")
        if not 0.0 <= self.cpu_load_ratio <= 1.0 or not 0.0 <= self.packet_loss_ratio <= 1.0:
            raise ValueError("peer_overlay_resource_ratio_invalid")
        if not 0 <= self.rtt_ms <= 120_000 or not 0 <= self.send_buffer_bytes <= 64 * 1024 * 1024:
            raise ValueError("peer_overlay_resource_value_invalid")
        if self.eligible_since_ms < 0:
            raise ValueError("peer_overlay_eligibility_time_invalid")

    @property
    def eligible_relay(self) -> bool:
        return bool(
            self.relay_consent
            and self.visible
            and self.battery not in {"unknown", "critical"}
            and self.network not in {"unknown", "constrained"}
            and self.effective_capacity >= 25
            and self.delivery_ratio >= 0.95
            and self.cpu_load_ratio <= 0.8
            and self.rtt_ms <= 500
            and self.packet_loss_ratio <= 0.05
            and self.send_buffer_bytes <= 2 * 1024 * 1024
            and not self.metered_network
        )

    @property
    def effective_capacity(self) -> int:
        return min(self.self_capacity, self.observed_capacity)

    @property
    def rank(self) -> tuple[int, int, int, int, int, str]:
        return (
            self.effective_capacity,
            int(self.delivery_ratio * 10_000),
            -int(self.packet_loss_ratio * 10_000),
            -self.rtt_ms,
            -self.send_buffer_bytes,
            self.peer_id,
        )


@dataclass(frozen=True, slots=True)
class PeerOverlayPlan:
    publication_id: str
    source_peer_id: str
    leases: tuple[PeerRouteLease, ...]
    rejected_peer_ids: tuple[str, ...]
    max_children: int
    destination_routes: tuple[tuple[str, str, str], ...]
    topology: str = "peer_data_dag"

    def as_dict(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "source_peer_id": self.source_peer_id,
            "leases": [{**lease.unsigned(), "signature": lease.signature} for lease in self.leases],
            "rejected_peer_ids": list(self.rejected_peer_ids),
            "max_children": self.max_children,
            "destination_routes": {
                local_peer_id: {
                    destination_peer_id: next_peer_id
                    for route_local, destination_peer_id, next_peer_id in self.destination_routes
                    if route_local == local_peer_id
                }
                for local_peer_id in sorted({route[0] for route in self.destination_routes})
            },
            "topology": self.topology,
        }


class PeerOverlayTopologyService:
    """Builds a DAG; peers only execute the exact signed edge they receive."""

    def __init__(
        self,
        signing_key: bytes,
        *,
        hub_key_id: str,
        max_children: int = 2,
        lease_seconds: int = 60,
        max_turn_edges: int = 1,
        minimum_eligible_ms: int = 15_000,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("peer_overlay_signing_key_too_short")
        if not 2 <= max_children <= 3:
            raise ValueError("peer_overlay_max_children_invalid")
        if (
            not 30 <= lease_seconds <= 300
            or not 0 <= max_turn_edges <= 16
            or not 0 <= minimum_eligible_ms <= 300_000
        ):
            raise ValueError("peer_overlay_budget_invalid")
        self._key = bytes(signing_key)
        self._hub_key_id = require_overlay_id(hub_key_id, "hub_key_id")
        self._max_children = max_children
        self._lease_seconds = lease_seconds
        self._max_turn_edges = max_turn_edges
        self._minimum_eligible_ms = minimum_eligible_ms

    def plan(
        self,
        *,
        tenant_id: str,
        room_id: str,
        publication_id: str,
        source_peer_id: str,
        candidates: Iterable[PeerOverlayCandidate | Mapping[str, Any]],
        epochs: OverlayEpochs,
        now: datetime | None = None,
    ) -> PeerOverlayPlan:
        tenant = require_overlay_id(tenant_id, "tenant_id")
        room = require_overlay_id(room_id, "room_id")
        publication = require_overlay_id(publication_id, "publication_id")
        source = require_overlay_id(source_peer_id, "source_peer_id")
        normalized = [_candidate(value) for value in candidates]
        by_id = {item.peer_id: item for item in normalized}
        if len(by_id) != len(normalized) or source not in by_id:
            raise ValueError("peer_overlay_candidate_set_invalid")
        ordered = sorted(
            (item for item in normalized if item.peer_id != source), key=lambda item: item.rank, reverse=True
        )
        connected = [source]
        depths = {source: 0}
        children = {item.peer_id: 0 for item in normalized}
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_ms = int(instant.timestamp() * 1_000)
        relay_eligible = {
            source,
            *(
                item.peer_id
                for item in normalized
                if item.eligible_relay and now_ms - item.eligible_since_ms >= self._minimum_eligible_ms
            ),
        }
        turn_edges = 0
        rejected: list[str] = []
        leases: list[PeerRouteLease] = []
        for child in ordered:
            parent_options = [
                peer_id
                for peer_id in connected
                if peer_id in relay_eligible and children[peer_id] < self._max_children and depths[peer_id] < 7
            ]
            if child.turn_required and turn_edges >= self._max_turn_edges:
                rejected.append(child.peer_id)
                continue
            if not parent_options:
                rejected.append(child.peer_id)
                continue
            parent_options.sort(key=lambda peer_id: (depths[peer_id], children[peer_id], peer_id))
            primary = parent_options[0]
            backup = next((item for item in parent_options[1:] if item != primary), None)
            depth = depths[primary] + 1
            lease_material = canonical_overlay_digest(
                {
                    "tenant_id": tenant,
                    "room_id": room,
                    "publication_id": publication,
                    "child_peer_id": child.peer_id,
                    "primary_parent_id": primary,
                    "backup_parent_id": backup,
                    "epochs": {
                        "membership": epochs.membership,
                        "key": epochs.key,
                        "route": epochs.route,
                        "topology": epochs.topology,
                    },
                }
            )
            lease = PeerRouteLease(
                version=1,
                lease_id=f"pol_{lease_material[:32]}",
                tenant_id=tenant,
                room_id=room,
                publication_id=publication,
                child_peer_id=child.peer_id,
                primary_parent_id=primary,
                backup_parent_id=backup,
                epochs=epochs,
                capabilities=(OverlayCapability.DATA_RELAY, OverlayCapability.TURN)
                if child.turn_required
                else (OverlayCapability.DATA_RELAY, OverlayCapability.DIRECT),
                traffic_classes=tuple(OverlayTrafficClass),
                max_hops=depth,
                issued_at=_iso(instant),
                expires_at=_iso(instant + timedelta(seconds=self._lease_seconds)),
                nonce=lease_material[32:],
                hub_key_id=self._hub_key_id,
            ).sign(self._key)
            leases.append(lease)
            children[primary] += 1
            depths[child.peer_id] = depth
            connected.append(child.peer_id)
            turn_edges += int(child.turn_required)
        destination_routes = _destination_routes(leases)
        return PeerOverlayPlan(
            publication,
            source,
            tuple(leases),
            tuple(sorted(rejected)),
            self._max_children,
            destination_routes,
        )


def _candidate(value: PeerOverlayCandidate | Mapping[str, Any]) -> PeerOverlayCandidate:
    return value if isinstance(value, PeerOverlayCandidate) else PeerOverlayCandidate(**dict(value))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _destination_routes(leases: Iterable[PeerRouteLease]) -> tuple[tuple[str, str, str], ...]:
    parent_by_child = {lease.child_peer_id: lease.primary_parent_id for lease in leases}
    routes: set[tuple[str, str, str]] = set()
    for destination in parent_by_child:
        next_peer = destination
        while next_peer in parent_by_child:
            local_peer = parent_by_child[next_peer]
            routes.add((local_peer, destination, next_peer))
            next_peer = local_peer
    return tuple(sorted(routes))


__all__ = ["PeerOverlayCandidate", "PeerOverlayPlan", "PeerOverlayTopologyService"]
