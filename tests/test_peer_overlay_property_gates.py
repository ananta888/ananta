from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.services.peer_overlay_topology_service import PeerOverlayCandidate, PeerOverlayTopologyService
from ananta_contracts.peer_overlay import OverlayEpochs, OverlayTrafficClass, PeerRouteLease

PROPERTY_SETTINGS = settings(max_examples=48, derandomize=True, database=None, deadline=None)
FIXED_NOW = datetime(2026, 8, 29, tzinfo=timezone.utc)
SAFE_CORPUS = ("tenant-property", "room-property", "publication-property", "peer-property")


@PROPERTY_SETTINGS
@given(
    membership=st.integers(min_value=1, max_value=10_000),
    key=st.integers(min_value=1, max_value=10_000),
    route=st.integers(min_value=1, max_value=10_000),
    topology=st.integers(min_value=1, max_value=10_000),
    change=st.sampled_from(("membership", "route", "topology")),
)
def test_generated_epoch_successors_change_only_the_hub_authorized_dimensions(
    membership: int, key: int, route: int, topology: int, change: str
) -> None:
    previous = OverlayEpochs(membership, key, route, topology)
    successors = {
        "membership": OverlayEpochs(membership + 1, key + 1, route, topology),
        "route": OverlayEpochs(membership, key, route + 1, topology),
        "topology": OverlayEpochs(membership, key, route + 1, topology + 1),
    }
    successors[change].assert_successor(previous, change=change)
    for other_change, candidate in successors.items():
        if other_change == change:
            continue
        with pytest.raises(ValueError, match="epoch_transition_invalid"):
            candidate.assert_successor(previous, change=change)


@PROPERTY_SETTINGS
@given(peer_count=st.integers(min_value=2, max_value=24), max_children=st.integers(min_value=2, max_value=3))
def test_generated_topologies_are_acyclic_scope_bound_and_memory_bounded(peer_count: int, max_children: int) -> None:
    candidates = [_candidate("source"), *(_candidate(f"peer-{index}") for index in range(peer_count - 1))]
    plan = PeerOverlayTopologyService(
        b"property-gate-signing-key-0000000", hub_key_id="hub-property", max_children=max_children
    ).plan(
        tenant_id=SAFE_CORPUS[0],
        room_id=SAFE_CORPUS[1],
        publication_id=SAFE_CORPUS[2],
        source_peer_id="source",
        candidates=candidates,
        epochs=OverlayEpochs(1, 1, 2, 2),
        now=FIXED_NOW,
    )
    assert len(plan.leases) <= peer_count - 1
    parent_by_child = {lease.child_peer_id: lease.primary_parent_id for lease in plan.leases}
    assert len(parent_by_child) == len(plan.leases)
    child_counts: dict[str, int] = {}
    for lease in plan.leases:
        assert lease.tenant_id == SAFE_CORPUS[0]
        assert lease.room_id == SAFE_CORPUS[1]
        assert lease.publication_id == SAFE_CORPUS[2]
        child_counts[lease.primary_parent_id] = child_counts.get(lease.primary_parent_id, 0) + 1
        visited = {lease.child_peer_id}
        cursor = lease.child_peer_id
        while cursor in parent_by_child:
            cursor = parent_by_child[cursor]
            assert cursor not in visited
            visited.add(cursor)
        assert cursor == "source"
    assert not child_counts or max(child_counts.values()) <= max_children


@PROPERTY_SETTINGS
@given(
    minimum_membership=st.integers(min_value=1, max_value=5),
    minimum_key=st.integers(min_value=1, max_value=5),
    minimum_route=st.integers(min_value=1, max_value=5),
    minimum_topology=st.integers(min_value=1, max_value=5),
)
def test_stale_authority_is_rejected_for_every_generated_epoch_floor(
    minimum_membership: int, minimum_key: int, minimum_route: int, minimum_topology: int
) -> None:
    lease = _lease(OverlayEpochs(1, 1, 1, 1))
    minimum = OverlayEpochs(minimum_membership, minimum_key, minimum_route, minimum_topology)
    arguments = {
        "expected_hub_key_id": "hub-property",
        "now": "2026-08-29T00:00:30Z",
        "tenant_id": SAFE_CORPUS[0],
        "room_id": SAFE_CORPUS[1],
        "publication_id": SAFE_CORPUS[2],
        "child_peer_id": SAFE_CORPUS[3],
        "minimum_epochs": minimum,
    }
    if minimum == OverlayEpochs(1, 1, 1, 1):
        lease.verify(b"property-gate-signing-key-0000000", **arguments)
    else:
        with pytest.raises(ValueError, match="epoch_stale"):
            lease.verify(b"property-gate-signing-key-0000000", **arguments)


def test_route_lease_kid_and_signed_scope_mutations_are_rejected() -> None:
    lease = _lease(OverlayEpochs(1, 1, 1, 1))
    verification = {
        "expected_hub_key_id": "hub-property",
        "now": "2026-08-29T00:00:30Z",
        "tenant_id": SAFE_CORPUS[0],
        "room_id": SAFE_CORPUS[1],
        "publication_id": SAFE_CORPUS[2],
        "child_peer_id": SAFE_CORPUS[3],
        "minimum_epochs": OverlayEpochs(1, 1, 1, 1),
    }
    with pytest.raises(ValueError, match="hub_key_unknown"):
        lease.verify(b"property-gate-signing-key-0000000", **{**verification, "expected_hub_key_id": "hub-old"})
    with pytest.raises(ValueError, match="signature_invalid"):
        replace(lease, publication_id="publication-forged").verify(b"property-gate-signing-key-0000000", **verification)


def test_python_and_typescript_traffic_class_contracts_have_exact_parity() -> None:
    source = (
        Path(__file__).parents[1] / "frontend-angular/src/app/services/peer-overlay/peer-overlay-traffic-policy.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"PEER_OVERLAY_DATA_CLASSES.*?Object\.freeze\(\[(.*?)\]\)", source, re.DOTALL)
    assert match is not None
    typescript_values = re.findall(r"'([^']+)'", match.group(1))
    assert typescript_values == [value.value for value in OverlayTrafficClass]
    assert not any(token in "\n".join(SAFE_CORPUS).lower() for token in ("secret", "token", "password", "private"))


def _candidate(peer_id: str) -> PeerOverlayCandidate:
    return PeerOverlayCandidate(
        peer_id=peer_id,
        relay_consent=True,
        visible=True,
        battery="mains",
        network="fast",
        self_capacity=90,
        observed_capacity=80,
        delivery_ratio=0.99,
        cpu_load_ratio=0.2,
        rtt_ms=30,
        packet_loss_ratio=0.001,
        send_buffer_bytes=0,
        metered_network=False,
    )


def _lease(epochs: OverlayEpochs) -> PeerRouteLease:
    return PeerRouteLease(
        version=1,
        lease_id="lease-property",
        tenant_id=SAFE_CORPUS[0],
        room_id=SAFE_CORPUS[1],
        publication_id=SAFE_CORPUS[2],
        child_peer_id=SAFE_CORPUS[3],
        primary_parent_id="parent-property",
        backup_parent_id=None,
        epochs=epochs,
        capabilities=("data_relay",),
        traffic_classes=tuple(OverlayTrafficClass),
        max_hops=2,
        issued_at="2026-08-29T00:00:00Z",
        expires_at="2026-08-29T00:01:00Z",
        nonce="nonce-property",
        hub_key_id="hub-property",
    ).sign(b"property-gate-signing-key-0000000")
