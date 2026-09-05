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
        "cpu_load_ratio": 0.2,
        "rtt_ms": 30,
        "packet_loss_ratio": 0.001,
        "send_buffer_bytes": 0,
        "metered_network": False,
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
    routes = first.as_dict()["destination_routes"]
    for child, parent in first_edges:
        assert routes[parent][child] == child
    deep_lease = next(item for item in first.leases if item.primary_parent_id != "source")
    assert routes["source"][deep_lease.child_peer_id] == deep_lease.primary_parent_id


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


def test_resource_pressure_metered_links_and_minimum_hold_prevent_relay_selection() -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    now_ms = int(now.timestamp() * 1_000)
    service = PeerOverlayTopologyService(b"t" * 32, hub_key_id="hub-1", max_children=2)
    plan = service.plan(
        tenant_id="tenant-1",
        room_id="room-1",
        publication_id="publication-1",
        source_peer_id="source",
        candidates=[
            candidate("source"),
            candidate("cpu", cpu_load_ratio=0.81),
            candidate("latency", rtt_ms=501),
            candidate("loss", packet_loss_ratio=0.051),
            candidate("buffer", send_buffer_bytes=2 * 1024 * 1024 + 1),
            candidate("metered", metered_network=True),
            candidate("warming", eligible_since_ms=now_ms - 14_999),
            candidate("stable", eligible_since_ms=now_ms - 15_000),
            candidate("leaf-a"),
            candidate("leaf-b"),
            candidate("leaf-c"),
        ],
        epochs=OverlayEpochs(1, 1, 2, 2),
        now=now,
    )
    parents = {lease.primary_parent_id for lease in plan.leases}
    assert "stable" in parents
    assert not {"cpu", "latency", "loss", "buffer", "metered", "warming"} & parents
