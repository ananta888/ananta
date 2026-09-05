from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent.services.peer_overlay_observability_service import PeerOverlayObservabilityService

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def plan(*, tenant: str, publication: str, age_seconds: int = 0, transport: str = "direct"):
    capabilities = ["data_relay", "turn" if transport == "turn" else "direct"]
    parent = "relay-secret" if transport == "peer_dag" else "source-secret"
    return {
        "tenant_id": tenant,
        "room_id": "room-secret",
        "publication_id": publication,
        "source_peer_id": "source-secret",
        "created_at": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "epochs": {"route": 7},
        "leases": [
            {
                "primary_parent_id": parent,
                "capabilities": capabilities,
                "sdp": "must-not-leak",
                "ice_credentials": "must-not-leak",
            }
        ],
        "cost_admission": {
            "allowed": True,
            "reason_code": "peer_overlay_cost_admitted",
            "profile_id": "profile-v1",
            "profile_version": "1.0.0",
            "projected_turn_egress_bytes": 0,
            "projected_peer_relay_egress_bytes": 10,
            "content_keys": "must-not-leak",
        },
    }


def membership(tenant: str):
    return {
        "tenant_id": tenant,
        "room_id": "room-secret",
        "member_ids": ["source-secret", "relay-secret"],
        "epochs": {"membership": 3, "key": 4},
        "updated_at": NOW.isoformat(),
    }


def test_operator_projection_lists_all_transport_classes_without_content() -> None:
    service = PeerOverlayObservabilityService(b"o" * 32)
    result = service.project(
        plans=[
            plan(tenant="tenant-1", publication="publication-direct"),
            plan(tenant="tenant-1", publication="publication-dag", transport="peer_dag"),
            plan(tenant="tenant-1", publication="publication-turn", transport="turn"),
            plan(tenant="tenant-2", publication="publication-other"),
        ],
        memberships=[membership("tenant-1"), membership("tenant-2")],
        tenant_id="tenant-1",
        room_id="room-secret",
        now=NOW,
    )

    assert {item["selected_transport"] for item in result["plans"]} == {"direct", "peer_dag", "turn"}
    assert set(result["plans"][0]["transports"]) == {"direct", "mesh", "peer_dag", "sfu", "turn"}
    assert result["aggregate"]["membership_count"] == 1
    serialized = json.dumps(result)
    for secret in (
        "tenant-1",
        "tenant-2",
        "room-secret",
        "publication-direct",
        "source-secret",
        "relay-secret",
        "must-not-leak",
    ):
        assert secret not in serialized


def test_projection_enforces_retention_cardinality_and_global_detail_suppression() -> None:
    service = PeerOverlayObservabilityService(b"o" * 32, retention_seconds=60, max_publications=1)
    plans = [
        plan(tenant="tenant-1", publication="publication-new"),
        plan(tenant="tenant-1", publication="publication-old", age_seconds=61),
        plan(tenant="tenant-1", publication="publication-second", age_seconds=1),
    ]

    tenant = service.project(plans=plans, memberships=[], tenant_id="tenant-1", room_id=None, now=NOW)
    global_view = service.project(plans=plans, memberships=[], tenant_id=None, room_id=None, now=NOW)

    assert tenant["aggregate"]["retained_publication_count"] == 2
    assert len(tenant["plans"]) == 1
    assert tenant["truncated"] is True
    assert global_view["plans"] == []
    assert global_view["aggregate"]["retained_publication_count"] == 2
    assert global_view["aggregate"]["selected_transport_counts"] == {"direct": 2}
