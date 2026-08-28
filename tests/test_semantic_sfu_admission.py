from __future__ import annotations

from dataclasses import replace
from typing import Any

import jwt
import pytest

from agent.services.media_topology_policy import MediaTopologyPolicy
from agent.services.semantic_fanout_coordination_service import (
    SemanticFanoutCoordinationService,
)
from agent.services.semantic_sfu_admission_service import (
    SemanticSfuAdmissionService,
    SfuAdmissionError,
    SfuMembership,
)

SECRET = "test-secret-that-is-longer-than-thirty-two-bytes"


class Memberships:
    def __init__(self) -> None:
        self.rows = {
            ("tenant-a", "session-a", "alice"): SfuMembership(
                "tenant-a", "session-a", "alice", "owner", 7,
                frozenset({"chat", "view_tui", "artifact_share"}),
            ),
            ("tenant-a", "session-a", "bob"): SfuMembership(
                "tenant-a", "session-a", "bob", "participant", 7,
                frozenset({"chat", "view_tui", "artifact_share"}),
            ),
            ("tenant-a", "session-a", "mallory"): SfuMembership(
                "tenant-a", "session-a", "mallory", "participant", 7,
                frozenset({"chat"}),
            ),
        }

    def member(self, *, tenant_id: str, session_id: str, participant_id: str):
        return self.rows.get((tenant_id, session_id, participant_id))


@pytest.fixture
def membership() -> Memberships:
    return Memberships()


@pytest.fixture
def service(membership: Memberships) -> SemanticSfuAdmissionService:
    return SemanticSfuAdmissionService(
        membership, enabled=True, public_ws_url="wss://sfu.example.test",
        api_key="test-api-key", api_secret=SECRET, token_ttl_seconds=45, clock=lambda: 1_000.0,
    )


def join(service: SemanticSfuAdmissionService, actor: str, revision: int = 0, key: str | None = None):
    return service.join(
        {
            "session_id": "session-a", "membership_epoch": 7, "expected_revision": revision,
            "idempotency_key": key or f"join-{actor}", "strict_e2ee": True, "e2ee_supported": True,
        }, actor_id=actor, tenant_id="tenant-a",
    )


def publication(service: SemanticSfuAdmissionService, revision: int, **overrides):
    body = {
        "session_id": "session-a", "membership_epoch": 7, "expected_revision": revision,
        "idempotency_key": "publish-camera", "publication_id": "camera-alice", "source": "camera",
        "kind": "video", "privacy": "ordinary", "authorized_subscriber_ids": ["bob"], "constraints": {
            "max_bitrate_bps": 1_000_000, "max_width": 1280, "max_height": 720, "max_fps": 30,
        },
    }
    body.update(overrides)
    return service.authorize_publication(body, actor_id="alice", tenant_id="tenant-a")


def test_join_token_is_short_lived_narrow_and_contains_no_lease(service: SemanticSfuAdmissionService):
    result = join(service, "alice")
    claims = jwt.decode(result["access_token"], SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert result["revision"] == 1
    assert claims["exp"] - claims["nbf"] <= 60
    assert claims["video"] == {
        "roomJoin": True, "room": result["room_id"], "canPublish": False,
        "canSubscribe": False, "canPublishData": False, "canPublishSources": [],
        "roomAdmin": False, "roomRecord": False,
    }
    assert claims["ananta_sfu"]["membership_epoch"] == 7
    assert claims["ananta_sfu"]["lease_authority"] is False
    assert claims["sub"] == result["livekit_identity"]
    assert claims["sub"] != "alice"
    assert "participant_id" not in claims["ananta_sfu"]
    assert "tenant_id" not in claims["ananta_sfu"]


def test_read_state_supports_restart_without_enumerating_other_participants(service: SemanticSfuAdmissionService):
    before = service.read_state(
        session_id="session-a", membership_epoch=7, actor_id="alice", tenant_id="tenant-a"
    )
    assert before == {
        "ok": True,
        "room_id": before["room_id"],
        "membership_epoch": 7,
        "revision": 0,
        "joined": False,
        "publications": [],
        "subscriptions": [],
    }
    joined = join(service, "alice")
    publication(service, joined["revision"])
    bob_state = service.read_state(
        session_id="session-a", membership_epoch=7, actor_id="bob", tenant_id="tenant-a"
    )
    assert bob_state["revision"] == 2 and bob_state["joined"] is False
    assert bob_state["publications"] == []
    alice_state = service.read_state(
        session_id="session-a", membership_epoch=7, actor_id="alice", tenant_id="tenant-a"
    )
    assert [row["publication_id"] for row in alice_state["publications"]] == ["camera-alice"]


def test_publication_intersects_source_permission_and_issues_only_camera(service: SemanticSfuAdmissionService):
    joined = join(service, "alice")
    result = publication(service, joined["revision"])
    claims = jwt.decode(result["access_token"], SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["video"]["canPublishSources"] == ["camera"]
    assert claims["video"]["canSubscribe"] is False
    assert result["publication"]["constraints"]["max_bitrate_bps"] == 1_000_000
    assert set(result["authorized_subscriber_livekit_identities"]) == {"bob"}
    assert result["authorized_subscriber_livekit_identities"]["bob"].startswith("lk_")


def test_publication_token_accumulates_only_current_hub_authorized_sources(
    service: SemanticSfuAdmissionService,
):
    joined = join(service, "alice")
    camera = publication(service, joined["revision"])
    microphone = publication(
        service,
        camera["revision"],
        idempotency_key="publish-microphone",
        publication_id="microphone-alice",
        source="microphone",
        kind="audio",
        constraints={"max_bitrate_bps": 128_000, "max_width": 0, "max_height": 0, "max_fps": 0},
    )
    claims = jwt.decode(
        microphone["access_token"], SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    assert claims["video"]["canPublishSources"] == ["camera", "microphone"]


def test_new_membership_epoch_atomically_invalidates_stale_room_projection(
    service: SemanticSfuAdmissionService, membership: Memberships
):
    alice = join(service, "alice")
    camera = publication(service, alice["revision"])
    bob = join(service, "bob", camera["revision"])
    subscribed = service.authorize_subscription(
        {
            "session_id": "session-a",
            "membership_epoch": 7,
            "expected_revision": bob["revision"],
            "idempotency_key": "subscribe-before-rollover",
            "subscription_id": "sub-before-rollover",
            "publication_id": "camera-alice",
        },
        actor_id="bob",
        tenant_id="tenant-a",
    )
    for identity in ("alice", "bob"):
        membership.rows[("tenant-a", "session-a", identity)] = replace(
            membership.rows[("tenant-a", "session-a", identity)], epoch=8
        )

    refreshed = service.join(
        {
            "session_id": "session-a",
            "membership_epoch": 8,
            "expected_revision": subscribed["revision"],
            "idempotency_key": "join-alice-epoch-8",
            "strict_e2ee": True,
            "e2ee_supported": True,
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )

    assert refreshed["membership_epoch"] == 8
    state = service.read_state(
        session_id="session-a", membership_epoch=8, actor_id="alice", tenant_id="tenant-a"
    )
    assert state["joined"] is True
    assert state["publications"] == []
    assert state["subscriptions"] == []


def test_publication_composes_hub_topology_and_one_upload_fanout(membership: Memberships):
    topology = RecordingTopologyPolicy()
    fanout = RecordingFanoutCoordination()
    composed = SemanticSfuAdmissionService(
        membership,
        enabled=True,
        public_ws_url="wss://sfu.example.test",
        api_key="test-api-key",
        api_secret=SECRET,
        token_ttl_seconds=45,
        clock=lambda: 1_000.0,
        topology_policy=topology,
        fanout=fanout,
    )
    joined = join(composed, "alice")

    admitted = publication(composed, joined["revision"])

    assert admitted["publication"]["authorized_subscriber_ids"] == ["bob"]
    assert len(topology.contexts) == 1
    assert topology.contexts[0].participant_count == 2
    assert topology.contexts[0].sfu_admitted is True
    assert len(fanout.calls) == 1
    assert fanout.calls[0]["publication_id"] == "camera-alice"
    assert [row.receiver_id for row in fanout.calls[0]["receivers"]] == ["bob"]


def test_publication_allows_maximum_broadcast_recipients(
    service: SemanticSfuAdmissionService,
    membership: Memberships,
):
    for recipient in ("carol", "dave", "erin", "frank", "gwen", "hank"):
        membership.rows[("tenant-a", "session-a", recipient)] = SfuMembership(
            "tenant-a", "session-a", recipient, "participant", 7, frozenset({"chat", "view_tui"}),
        )
    joined = join(service, "alice")
    result = service.authorize_publication(
        {
            "session_id": "session-a", "membership_epoch": 7, "expected_revision": joined["revision"],
                "idempotency_key": "publish-max", "publication_id": "camera-alice-max", "source": "camera",
                "kind": "video", "privacy": "ordinary", "authorized_subscriber_ids": [
                    "bob", "carol", "dave", "erin", "frank", "gwen", "hank",
                ], "constraints": {
                    "max_bitrate_bps": 1_000_000, "max_width": 1280, "max_height": 720, "max_fps": 30,
                },
            }, actor_id="alice", tenant_id="tenant-a",
        )
    assert result["publication"]["authorized_subscriber_ids"] == [
        "bob", "carol", "dave", "erin", "frank", "gwen", "hank",
    ]


def test_publication_rejects_broadcast_recipients_beyond_cap(
    service: SemanticSfuAdmissionService,
    membership: Memberships,
):
    for recipient in ("carol", "dave", "erin", "frank", "gwen", "hank", "ivan", "jane"):
        membership.rows[("tenant-a", "session-a", recipient)] = SfuMembership(
            "tenant-a", "session-a", recipient, "participant", 7, frozenset({"chat"}),
        )
    joined = join(service, "alice")
    with pytest.raises(SfuAdmissionError, match="capacity_cap_exceeded"):
        service.authorize_publication(
            {
                "session_id": "session-a", "membership_epoch": 7, "expected_revision": joined["revision"],
                "idempotency_key": "publish-too-many", "publication_id": "camera-alice-over", "source": "camera",
                "kind": "video", "privacy": "ordinary", "authorized_subscriber_ids": [
                    "bob", "carol", "dave", "erin", "frank", "gwen", "hank", "ivan", "jane",
                ], "constraints": {
                    "max_bitrate_bps": 1_000_000, "max_width": 1280, "max_height": 720, "max_fps": 30,
                },
            },
            actor_id="alice", tenant_id="tenant-a",
        )


def test_topology_denial_precedes_publication_state_mutation(membership: Memberships):
    topology = RecordingTopologyPolicy(deny=True)
    composed = SemanticSfuAdmissionService(
        membership,
        enabled=True,
        public_ws_url="wss://sfu.example.test",
        api_key="test-api-key",
        api_secret=SECRET,
        token_ttl_seconds=45,
        clock=lambda: 1_000.0,
        topology_policy=topology,
        fanout=RecordingFanoutCoordination(),
    )
    joined = join(composed, "alice")

    with pytest.raises(SfuAdmissionError, match="sfu_topology_policy_denied"):
        publication(composed, joined["revision"])

    state = composed.read_state(
        session_id="session-a",
        membership_epoch=7,
        actor_id="alice",
        tenant_id="tenant-a",
    )
    assert state["revision"] == joined["revision"]
    assert state["publications"] == []


def test_subscription_requires_join_current_revision_and_existing_publication(service: SemanticSfuAdmissionService):
    first = join(service, "alice")
    published = publication(service, first["revision"])
    bob = join(service, "bob", published["revision"])
    result = service.authorize_subscription(
        {
            "session_id": "session-a", "membership_epoch": 7, "expected_revision": bob["revision"],
            "idempotency_key": "subscribe-bob", "subscription_id": "sub-bob-camera",
            "publication_id": "camera-alice",
        }, actor_id="bob", tenant_id="tenant-a",
    )
    assert result["subscription"]["publisher_id"] == "alice"
    claims = jwt.decode(result["access_token"], SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["video"]["canSubscribe"] is True
    assert claims["video"]["canPublish"] is False


def test_idempotency_replays_exact_result_and_rejects_changed_request(service: SemanticSfuAdmissionService):
    first = join(service, "alice", key="stable-key")
    again = join(service, "alice", key="stable-key")
    assert again == first
    with pytest.raises(SfuAdmissionError, match="sfu_idempotency_conflict"):
        join(service, "alice", revision=1, key="stable-key")


@pytest.mark.parametrize(
    ("body", "actor", "tenant", "reason"),
    [
        ({"session_id": "session-a", "membership_epoch": 6, "expected_revision": 0,
          "idempotency_key": "stale", "strict_e2ee": True, "e2ee_supported": True},
         "alice", "tenant-a", "sfu_membership_epoch_stale"),
        ({"session_id": "session-a", "membership_epoch": 7, "expected_revision": 0,
          "idempotency_key": "idor", "strict_e2ee": True, "e2ee_supported": True},
         "alice", "tenant-b", "sfu_membership_required"),
        ({"session_id": "session-a", "membership_epoch": 7, "expected_revision": 0,
          "idempotency_key": "no-e2ee", "strict_e2ee": True, "e2ee_supported": False},
         "alice", "tenant-a", "sfu_e2ee_capability_required"),
    ],
)
def test_join_rejects_stale_cross_tenant_and_strict_downgrade(service, body, actor, tenant, reason):
    with pytest.raises(SfuAdmissionError, match=reason):
        service.join(body, actor_id=actor, tenant_id=tenant)


def test_private_recovery_is_only_subscribable_by_bound_audience(service: SemanticSfuAdmissionService):
    joined = join(service, "alice")
    published = publication(
        service, joined["revision"], idempotency_key="private-pub", publication_id="private-a-b",
        privacy="private_recovery", audience_participant_id="bob", authorized_subscriber_ids=["bob"],
    )
    bob = join(service, "bob", published["revision"])
    allowed = service.authorize_subscription(
        {"session_id": "session-a", "membership_epoch": 7, "expected_revision": bob["revision"],
         "idempotency_key": "private-bob", "subscription_id": "private-sub-b", "publication_id": "private-a-b"},
        actor_id="bob", tenant_id="tenant-a",
    )
    mallory = join(service, "mallory", allowed["revision"])
    with pytest.raises(SfuAdmissionError, match="sfu_private_recovery_forbidden"):
        service.authorize_subscription(
            {"session_id": "session-a", "membership_epoch": 7, "expected_revision": mallory["revision"],
             "idempotency_key": "private-mallory", "subscription_id": "private-sub-m",
             "publication_id": "private-a-b"}, actor_id="mallory", tenant_id="tenant-a",
        )


def test_revoke_epoch_or_leave_prevents_reuse(service: SemanticSfuAdmissionService, membership: Memberships):
    joined = join(service, "alice")
    left = service.leave(
        {"session_id": "session-a", "membership_epoch": 7, "expected_revision": joined["revision"],
         "idempotency_key": "leave-alice"}, actor_id="alice", tenant_id="tenant-a",
    )
    assert left["reason_code"] == "sfu_participant_left"
    membership.rows[("tenant-a", "session-a", "alice")] = replace(
        membership.rows[("tenant-a", "session-a", "alice")], epoch=8,
    )
    with pytest.raises(SfuAdmissionError, match="sfu_membership_epoch_stale"):
        publication(service, left["revision"])


def test_disabled_or_weak_configuration_fails_closed(membership: Memberships):
    disabled = SemanticSfuAdmissionService(
        membership, enabled=False, public_ws_url="wss://sfu.test", api_key="key", api_secret=SECRET,
    )
    with pytest.raises(SfuAdmissionError, match="sfu_disabled"):
        join(disabled, "alice")
    weak = SemanticSfuAdmissionService(
        membership, enabled=True, public_ws_url="wss://sfu.test", api_key="key", api_secret="weak",
    )
    with pytest.raises(SfuAdmissionError, match="sfu_configuration_invalid"):
        join(weak, "alice")


class RecordingTopologyPolicy:
    def __init__(self, *, deny: bool = False) -> None:
        self._delegate = MediaTopologyPolicy()
        self._deny = deny
        self.contexts: list[Any] = []

    def decide(self, context):
        self.contexts.append(context)
        decision = self._delegate.decide(context)
        return replace(decision, target="ordinary_direct") if self._deny else decision


class RecordingFanoutCoordination:
    def __init__(self) -> None:
        self._delegate = SemanticFanoutCoordinationService()
        self.calls: list[dict[str, Any]] = []

    def plan(self, **kwargs):
        self.calls.append(kwargs)
        return self._delegate.plan(**kwargs)
