from __future__ import annotations

import base64
import hashlib
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
from sqlmodel import SQLModel

from agent.database import engine
from agent.repositories.semantic_relay_repository import InMemorySemanticRelayRepository
from agent.repositories.webrtc_epoch_repository import WebrtcEpochRepository
from agent.routes import semantic_sfu_admission as sfu_routes
from agent.services.semantic_relay_limits import SemanticRelayLimits
from agent.services.semantic_sfu_admission_service import SfuMembership
from agent.services.semantic_sfu_group_key_service import SemanticSfuGroupKeyService, SfuGroupKeyError
from agent.services.share_relay_compatibility_service import ShareRelayCompatibilityService
from agent.services.user_session_tokens import issue_user_access_token
from agent.services.webrtc_epoch_service import WebrtcEpochService
from agent.services.webrtc_group_key_authorization_service import WebrtcGroupKeyAuthorizationService


class Memberships:
    def __init__(self, epoch: int = 7) -> None:
        self.rows = {
            member: SfuMembership(
                "tenant-a",
                "session-a",
                member,
                "owner" if member == "alice" else "participant",
                epoch,
                frozenset({"chat"}),
            )
            for member in ("alice", "bob", "carol")
        }

    def member(self, *, tenant_id: str, session_id: str, participant_id: str):
        row = self.rows.get(participant_id)
        if row and row.tenant_id == tenant_id and row.session_id == session_id:
            return row
        return None


class Publications:
    def __init__(self, room_id: str, subscribers: list[str], epoch: int = 7) -> None:
        self.room_id = room_id
        self.subscribers = subscribers
        self.epoch = epoch

    def publication_for_group_key(self, **values):
        if values["actor_id"] != "alice" or values["membership_epoch"] != self.epoch:
            raise SfuGroupKeyError("sfu_group_membership_epoch_stale", 409)
        return {
            "publication_id": values["publication_id"],
            "participant_id": "alice",
            "room_id": self.room_id,
            "membership_epoch": self.epoch,
            "authorized_subscriber_ids": list(self.subscribers),
        }


@pytest.fixture
def group_stack():
    SQLModel.metadata.create_all(engine)
    room_id = f"sfu-{uuid.uuid4().hex}"
    memberships = Memberships()
    publications = Publications(room_id, ["bob", "carol"])
    epochs = WebrtcEpochService(WebrtcEpochRepository(), clock=lambda: 1_000.0)
    authorization = WebrtcGroupKeyAuthorizationService(
        private_key=Ed25519PrivateKey.generate(),
        hub_key_id="hub-key",
        epoch_repository=WebrtcEpochRepository(),
        clock=lambda: 1_000.0,
    )
    relay = ShareRelayCompatibilityService(
        InMemorySemanticRelayRepository(SemanticRelayLimits(max_batch_count=250)),
        clock=lambda: 1_000.0,
    )
    service = SemanticSfuGroupKeyService(
        membership=memberships,
        publications=publications,
        epochs=epochs,
        authorization=authorization,
        relay=relay,
        hub_id="hub-a",
        clock=lambda: 1_000.0,
    )
    return service, memberships, publications


def _prepare(service: SemanticSfuGroupKeyService, members=("alice", "bob", "carol"), key="prepare-1"):
    return service.prepare_epoch(
        {
            "session_id": "session-a",
            "membership_epoch": 7,
            "publication_id": "microphone-alice",
            "key_package_refs": {member: f"pkg-{member}-{key}" for member in members},
            "idempotency_key": key,
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )


def _opaque(recipient: str, package_ref: str) -> dict[str, object]:
    ciphertext = (f"opaque-aes-gcm:{recipient}:".encode() + b"x" * 96)[:96]
    return {
        "recipient_id": recipient,
        "package_ref": package_ref,
        "opaque_package_b64": base64.b64encode(ciphertext).decode("ascii"),
        "package_digest": hashlib.sha256(ciphertext).hexdigest(),
        "expires_at_ms": 1_100_000,
    }


def test_signed_epoch_routes_only_opaque_packages_and_tracks_receivers_independently(group_stack) -> None:
    service, _memberships, _publications = group_stack
    prepared = _prepare(service)
    authorization = prepared["authorization"]
    assert authorization["membership_epoch"] == 7
    assert authorization["member_ids"] == ["alice", "bob", "carol"]
    assert "content_key" not in repr(prepared)

    refs = authorization["key_package_refs"]
    delivered = service.deliver_packages(
        authorization["authorization_id"],
        {
            "packages": [_opaque("bob", refs["bob"]), _opaque("carol", refs["carol"])],
            "idempotency_key": "deliver-1",
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )
    assert delivered["pending_member_ids"] == []
    bob = service.read_packages(
        session_id="session-a", membership_epoch=7, cursor="", actor_id="bob", tenant_id="tenant-a"
    )
    assert len(bob["packages"]) == 1
    assert bob["packages"][0]["recipient_id"] == "bob"
    assert bob["packages"][0]["publisher_id"] == "alice"
    assert "content_key" not in repr(bob)

    service.acknowledge_package(
        authorization["authorization_id"],
        {"package_ref": refs["bob"], "membership_epoch": 7},
        actor_id="bob",
        tenant_id="tenant-a",
    )
    status = service.epoch_status(authorization["authorization_id"], actor_id="alice", tenant_id="tenant-a")
    assert status["acknowledged_member_ids"] == ["bob"]
    assert status["pending_member_ids"] == ["carol"]


def test_late_join_gets_only_new_group_epoch_and_stale_membership_is_rejected(group_stack) -> None:
    service, memberships, publications = group_stack
    publications.subscribers = ["bob"]
    first = _prepare(service, members=("alice", "bob"))
    assert first["authorization"]["epoch"] == 1

    for member, row in list(memberships.rows.items()):
        memberships.rows[member] = SfuMembership(
            row.tenant_id, row.session_id, row.participant_id, row.role, 8, row.permissions
        )
    publications.epoch = 8
    publications.subscribers = ["bob", "carol"]
    second = service.prepare_epoch(
        {
            "session_id": "session-a",
            "membership_epoch": 8,
            "publication_id": "microphone-alice",
            "key_package_refs": {member: f"pkg-{member}-epoch-8" for member in ("alice", "bob", "carol")},
            "idempotency_key": "prepare-epoch-8",
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )
    assert second["authorization"]["epoch"] == 2
    assert second["authorization"]["reason"] == "join"
    with pytest.raises(SfuGroupKeyError, match="membership_epoch_stale"):
        service.read_packages(
            session_id="session-a", membership_epoch=7, cursor="", actor_id="bob", tenant_id="tenant-a"
        )


def test_revoked_member_cannot_poll_or_enter_rekeyed_member_set(group_stack) -> None:
    service, memberships, publications = group_stack
    _prepare(service)
    memberships.rows.pop("bob")
    for member, row in list(memberships.rows.items()):
        memberships.rows[member] = SfuMembership(
            row.tenant_id, row.session_id, row.participant_id, row.role, 8, row.permissions
        )
    publications.epoch = 8
    publications.subscribers = ["carol"]
    rekeyed = service.prepare_epoch(
        {
            "session_id": "session-a",
            "membership_epoch": 8,
            "publication_id": "microphone-alice",
            "key_package_refs": {"alice": "pkg-a-revoke", "carol": "pkg-c-revoke"},
            "idempotency_key": "prepare-revoke",
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )
    assert rekeyed["authorization"]["member_ids"] == ["alice", "carol"]
    assert rekeyed["authorization"]["reason"] == "revoke"
    with pytest.raises(SfuGroupKeyError, match="membership_required"):
        service.read_packages(
            session_id="session-a", membership_epoch=8, cursor="", actor_id="bob", tenant_id="tenant-a"
        )


def test_authenticated_route_keeps_payload_contract_closed(monkeypatch) -> None:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sfu_routes.semantic_sfu_admission_bp)

    class Service:
        def prepare_epoch(self, body, *, actor_id, tenant_id):
            assert set(body) == {
                "session_id",
                "membership_epoch",
                "publication_id",
                "key_package_refs",
                "idempotency_key",
            }
            assert actor_id == "group-user" and tenant_id == "group-user"
            return {"ok": True, "authorization": {"membership_epoch": 3, "epoch": 2}}

    monkeypatch.setattr(sfu_routes, "get_semantic_sfu_group_key_service", Service)
    token = issue_user_access_token(username="group-user", role="admin")
    response = app.test_client().post(
        "/v1/semantic-media/sfu/group-keys/epochs",
        json={
            "session_id": "session-a",
            "membership_epoch": 3,
            "publication_id": "pub-a",
            "key_package_refs": {"group-user": "pkg-a", "bob": "pkg-b"},
            "idempotency_key": "route-1",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.get_json()["authorization"] == {"epoch": 2, "membership_epoch": 3}


def test_concurrent_hub_is_fenced_then_failover_rekeys_and_hides_stale_packages() -> None:
    now = [1_000.0]
    room_id = f"sfu-failover-{uuid.uuid4().hex}"
    memberships = Memberships()
    publications = Publications(room_id, ["bob", "carol"])
    epoch_repository = WebrtcEpochRepository()
    relay = ShareRelayCompatibilityService(
        InMemorySemanticRelayRepository(SemanticRelayLimits(max_batch_count=250)),
        clock=lambda: now[0],
    )
    private_key = Ed25519PrivateKey.generate()

    def make_service(hub_id: str) -> SemanticSfuGroupKeyService:
        epochs = WebrtcEpochService(epoch_repository, clock=lambda: now[0])
        return SemanticSfuGroupKeyService(
            membership=memberships,
            publications=publications,
            epochs=epochs,
            authorization=WebrtcGroupKeyAuthorizationService(
                private_key=private_key,
                hub_key_id="hub-key",
                epoch_repository=epoch_repository,
                clock=lambda: now[0],
            ),
            relay=relay,
            hub_id=hub_id,
            clock=lambda: now[0],
        )

    hub_a = make_service("hub-a")
    first = _prepare(hub_a, key="hub-a-first")
    first_auth = first["authorization"]
    first_refs = first_auth["key_package_refs"]
    hub_a.deliver_packages(
        first_auth["authorization_id"],
        {
            "packages": [
                _opaque("bob", first_refs["bob"]),
                _opaque("carol", first_refs["carol"]),
            ],
            "idempotency_key": "deliver-hub-a",
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )

    hub_b = make_service("hub-b")
    with pytest.raises(SfuGroupKeyError, match="epoch_split_brain"):
        _prepare(hub_b, key="hub-b-too-early")

    now[0] += 31
    second = _prepare(hub_b, key="hub-b-failover")
    second_auth = second["authorization"]
    assert second_auth["reason"] == "hub_failover"
    assert second_auth["epoch"] == first_auth["epoch"] + 1
    second_refs = second_auth["key_package_refs"]
    hub_b.deliver_packages(
        second_auth["authorization_id"],
        {
            "packages": [
                _opaque("bob", second_refs["bob"]),
                _opaque("carol", second_refs["carol"]),
            ],
            "idempotency_key": "deliver-hub-b",
        },
        actor_id="alice",
        tenant_id="tenant-a",
    )
    bob = hub_b.read_packages(
        session_id="session-a",
        membership_epoch=7,
        cursor="",
        actor_id="bob",
        tenant_id="tenant-a",
    )
    assert [row["package_ref"] for row in bob["packages"]] == [second_refs["bob"]]
    with pytest.raises(SfuGroupKeyError, match="authorization_stale"):
        hub_a.epoch_status(first_auth["authorization_id"], actor_id="alice", tenant_id="tenant-a")
