from __future__ import annotations

from datetime import datetime, timezone

from agent.services.peer_overlay_topology_service import PeerOverlayCandidate, PeerOverlayTopologyService
from ananta_contracts.peer_overlay import OverlayEpochs


def candidate(peer_id: str, **changes) -> PeerOverlayCandidate:
    values = {
        "peer_id": peer_id,
        "relay_consent": True,
        "visible": True,
        "battery": "mains",
        "network": "fast",
        "self_capacity": 90,
        "observed_capacity": 80,
        "delivery_ratio": 0.99,
    }
    values.update(changes)
    return PeerOverlayCandidate(**values)


def test_dag_is_deterministic_acyclic_bounded_and_publication_scoped() -> None:
    service = PeerOverlayTopologyService(b"t" * 32, hub_key_id="hub-1", max_children=2)
    values = [candidate("source"), *(candidate(f"peer-{index}") for index in range(1, 7))]
    arguments = {
        "tenant_id": "tenant-1",
        "room_id": "room-1",
        "publication_id": "publication-1",
        "source_peer_id": "source",
        "candidates": values,
        "epochs": OverlayEpochs(1, 1, 2, 2),
        "now": datetime(2026, 8, 29, tzinfo=timezone.utc),
    }
    first = service.plan(**arguments)
    second = service.plan(**arguments)
    assert first.as_dict() == second.as_dict()
    first_edges = [(item.child_peer_id, item.primary_parent_id) for item in first.leases]
    second_edges = [(item.child_peer_id, item.primary_parent_id) for item in second.leases]
    assert first_edges == second_edges
    child_counts: dict[str, int] = {}
    seen = {"source"}
    for child, parent in first_edges:
        assert parent in seen
        seen.add(child)
        child_counts[parent] = child_counts.get(parent, 0) + 1
    assert max(child_counts.values()) <= 2


def test_untrusted_capacity_and_missing_consent_cannot_create_relay_authority() -> None:
    service = PeerOverlayTopologyService(b"t" * 32, hub_key_id="hub-1", max_children=2)
    plan = service.plan(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=[
            candidate("source"),
            candidate("untrusted", self_capacity=100, observed_capacity=5),
            candidate("no-consent", relay_consent=False),
            candidate("child-a"),
            candidate("child-b"),
            candidate("child-c"),
        ],
        epochs=OverlayEpochs(1, 1, 2, 2),
        now=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    parents = {lease.primary_parent_id for lease in plan.leases}
    assert "untrusted" not in parents
    assert "no-consent" not in parents
