from __future__ import annotations

import uuid

from agent.repositories.semantic_sfu_admission_repository import (
    SqlSfuAdmissionStateRepository,
)
from agent.services.semantic_sfu_admission_service import (
    SemanticSfuAdmissionService,
    SfuMembership,
)

SECRET = "persistent-sfu-test-secret-longer-than-thirty-two-bytes"


class Memberships:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def member(self, *, tenant_id: str, session_id: str, participant_id: str):
        if tenant_id != "tenant-sfu-persistent" or session_id != self.session_id:
            return None
        if participant_id not in {"alice", "bob"}:
            return None
        return SfuMembership(
            tenant_id,
            session_id,
            participant_id,
            "owner" if participant_id == "alice" else "participant",
            4,
            frozenset({"chat", "view_tui"}),
        )


def service(session_id: str, repository: SqlSfuAdmissionStateRepository):
    return SemanticSfuAdmissionService(
        Memberships(session_id),
        enabled=True,
        public_ws_url="wss://sfu.example.test",
        api_key="persistent-test-key",
        api_secret=SECRET,
        token_ttl_seconds=45,
        clock=lambda: 1_000.0,
        state_repository=repository,
    )


def test_two_hub_instances_share_room_projection_and_exact_idempotency_receipt() -> None:
    session_id = f"persistent-{uuid.uuid4().hex}"
    repository = SqlSfuAdmissionStateRepository(clock=lambda: 1_000.0)
    hub_a = service(session_id, repository)
    hub_b = service(session_id, SqlSfuAdmissionStateRepository(clock=lambda: 1_000.0))
    request = {
        "session_id": session_id,
        "membership_epoch": 4,
        "expected_revision": 0,
        "idempotency_key": "join-across-hubs",
        "strict_e2ee": True,
        "e2ee_supported": True,
    }
    first = hub_a.join(request, actor_id="alice", tenant_id="tenant-sfu-persistent")
    replay = hub_b.join(request, actor_id="alice", tenant_id="tenant-sfu-persistent")
    assert replay == first
    state = hub_b.read_state(
        session_id=session_id,
        membership_epoch=4,
        actor_id="alice",
        tenant_id="tenant-sfu-persistent",
    )
    assert state["revision"] == 1 and state["joined"] is True


def test_room_revision_cas_fences_concurrent_hubs_and_restart_preserves_publication() -> None:
    session_id = f"cas-{uuid.uuid4().hex}"
    repository_a = SqlSfuAdmissionStateRepository(clock=lambda: 1_000.0)
    repository_b = SqlSfuAdmissionStateRepository(clock=lambda: 1_000.0)
    hub_a = service(session_id, repository_a)
    joined = hub_a.join(
        {
            "session_id": session_id,
            "membership_epoch": 4,
            "expected_revision": 0,
            "idempotency_key": "join-cas",
            "strict_e2ee": True,
            "e2ee_supported": True,
        },
        actor_id="alice",
        tenant_id="tenant-sfu-persistent",
    )
    published = hub_a.authorize_publication(
        {
            "session_id": session_id,
            "membership_epoch": 4,
            "expected_revision": joined["revision"],
            "idempotency_key": "publish-cas",
            "publication_id": "camera-alice",
            "source": "camera",
            "kind": "video",
            "privacy": "ordinary",
            "authorized_subscriber_ids": ["bob"],
            "constraints": {
                "max_bitrate_bps": 1_000_000,
                "max_width": 1280,
                "max_height": 720,
                "max_fps": 30,
            },
        },
        actor_id="alice",
        tenant_id="tenant-sfu-persistent",
    )
    restarted = service(session_id, repository_b)
    state = restarted.read_state(
        session_id=session_id,
        membership_epoch=4,
        actor_id="alice",
        tenant_id="tenant-sfu-persistent",
    )
    assert state["revision"] == published["revision"] == 2
    assert [row["publication_id"] for row in state["publications"]] == ["camera-alice"]

    left = repository_a.load("tenant-sfu-persistent", session_id)
    right = repository_b.load("tenant-sfu-persistent", session_id)
    assert left is not None and right is not None
    left.revision += 1
    right.revision += 1
    left.participants["left-writer"] = 4
    right.participants["right-writer"] = 4
    assert repository_a.compare_and_swap("tenant-sfu-persistent", session_id, expected_revision=2, state=left)
    assert not repository_b.compare_and_swap("tenant-sfu-persistent", session_id, expected_revision=2, state=right)


def test_expired_receipts_are_bounded_and_pruned_without_room_loss() -> None:
    session_id = f"prune-{uuid.uuid4().hex}"
    now = [1_000.0]
    repository = SqlSfuAdmissionStateRepository(clock=lambda: now[0])
    hub = service(session_id, repository)
    # Service clock is fixed to 1000; the receipt therefore expires at 1045.
    hub.join(
        {
            "session_id": session_id,
            "membership_epoch": 4,
            "expected_revision": 0,
            "idempotency_key": "join-prune",
            "strict_e2ee": True,
            "e2ee_supported": True,
        },
        actor_id="alice",
        tenant_id="tenant-sfu-persistent",
    )
    now[0] = 1_046.0
    assert repository.prune() == 1
    state = repository.load("tenant-sfu-persistent", session_id)
    assert state is not None and state.revision == 1
