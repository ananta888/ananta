from datetime import UTC, datetime, timedelta

import pytest

from agent.services.turn_pool_directory import (
    TurnPoolDirectory,
    TurnPoolDirectoryError,
    TurnPoolNode,
    TurnPoolSelectionQuery,
)


NOW = datetime(2026, 7, 22, tzinfo=UTC)


class Repository:
    def __init__(self, nodes):
        self.nodes = nodes

    def list_pool(self, *, pool_id, region):
        return [node for node in self.nodes if node.pool_id == pool_id and node.region == region]


def node(instance_id, *, fresh=True, capacity="accept", cost=10):
    return TurnPoolNode(
        pool_id="pool-a",
        instance_id=instance_id,
        region="eu-central",
        endpoints=({"url": f"turn:turn-{instance_id}.example:3478", "consumer": "peer", "transport": "udp"},),
        credential_modes=("rest_hmac_sha256",),
        config_version="cfg-1",
        config_digest="sha256:" + "a" * 64,
        observer_identity_id=f"observer-{instance_id}",
        observer_identity_version=1,
        trust_policy_version="trust-1",
        lifecycle_state="active",
        health_status="healthy",
        relay_ready=True,
        capacity_status=capacity,
        cost_units=cost,
        fresh_until=NOW + timedelta(seconds=30) if fresh else NOW,
        observation_fencing_token=3,
        version=2,
    )


def query(**changes):
    values = dict(
        pool_id="pool-a",
        region="eu-central",
        consumer="peer",
        transport="udp",
        credential_mode="rest_hmac_sha256",
        config_version="cfg-1",
        trust_policy_version="trust-1",
        receiver_stability_ref="receiver-digest",
    )
    values.update(changes)
    return TurnPoolSelectionQuery(**values)


def test_selection_ignores_stale_and_stopped_nodes():
    service = TurnPoolDirectory(
        Repository([node("stale", fresh=False), node("stopped", capacity="stop"), node("ready")]),
        selection_hmac_key=b"k" * 32,
        observer_is_active=lambda identity_id, version: True,
        now=lambda: NOW,
    )

    assert service.select(query()).instance_id == "ready"


def test_failover_requires_exact_bounded_exclusion_state():
    service = TurnPoolDirectory(
        Repository([node("a"), node("b")]),
        selection_hmac_key=b"k" * 32,
        observer_is_active=lambda identity_id, version: True,
        now=lambda: NOW,
    )
    first = service.select(query())
    second = service.select(
        query(excluded_instance_ids=(first.instance_id,), retry_index=1)
    )

    assert second.instance_id != first.instance_id
    with pytest.raises(TurnPoolDirectoryError, match="turn_pool_failover_state_invalid"):
        service.select(query(excluded_instance_ids=("a",), retry_index=0))


def test_revoked_observer_is_ineligible_even_while_observation_is_fresh():
    service = TurnPoolDirectory(
        Repository([node("revoked")]),
        selection_hmac_key=b"k" * 32,
        observer_is_active=lambda identity_id, version: False,
        now=lambda: NOW,
    )

    with pytest.raises(TurnPoolDirectoryError, match="turn_pool_no_eligible_instance"):
        service.select(query())
