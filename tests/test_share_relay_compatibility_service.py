from __future__ import annotations

from agent.repositories.semantic_relay_repository import InMemorySemanticRelayRepository
from agent.services.semantic_relay_limits import SemanticRelayLimits
from agent.services.share_relay_compatibility_service import ShareRelayCompatibilityService


def _service() -> tuple[ShareRelayCompatibilityService, InMemorySemanticRelayRepository]:
    limits = SemanticRelayLimits(max_batch_count=250)
    repository = InMemorySemanticRelayRepository(limits)
    return ShareRelayCompatibilityService(repository, clock=lambda: 1), repository


def test_legacy_shape_is_fanned_out_with_independent_audience_cursor() -> None:
    service, _repository = _service()
    item = {"message_id": "m1", "kind": "delta", "encrypted_payload": "opaque"}
    assert (
        service.publish(
            tenant_id="tenant",
            session_id="session",
            epoch=1,
            sender_id="owner",
            audience_ids=["alice", "bob", "alice"],
            traffic_class="visual_semantic",
            item=item,
            item_id_field="message_id",
            queue_limit=50,
        )
        == 2
    )
    alice, alice_cursor = service.read(
        tenant_id="tenant",
        session_id="session",
        audience_id="alice",
        traffic_class="visual_semantic",
        since_item_id="",
        item_id_field="message_id",
        queue_limit=50,
        page_limit=10,
    )
    bob, _ = service.read(
        tenant_id="tenant",
        session_id="session",
        audience_id="bob",
        traffic_class="visual_semantic",
        since_item_id="",
        item_id_field="message_id",
        queue_limit=50,
        page_limit=10,
    )
    assert alice == bob == [item]
    assert alice_cursor == "m1"
    assert (
        service.read(
            tenant_id="tenant",
            session_id="session",
            audience_id="alice",
            traffic_class="visual_semantic",
            since_item_id="m1",
            item_id_field="message_id",
            queue_limit=50,
            page_limit=10,
        )[0]
        == []
    )
    assert service.read(
        tenant_id="tenant",
        session_id="session",
        audience_id="bob",
        traffic_class="visual_semantic",
        since_item_id="",
        item_id_field="message_id",
        queue_limit=50,
        page_limit=10,
    )[0] == [item]


def test_retention_trim_since_and_clear_are_bounded_and_repeatable() -> None:
    service, repository = _service()
    for index in range(4):
        service.publish(
            tenant_id="tenant",
            session_id="session",
            epoch=1,
            sender_id="owner",
            audience_ids=["alice"],
            traffic_class="transcript",
            item={"id": f"m{index}", "encrypted_payload": "opaque"},
            item_id_field="id",
            queue_limit=3,
        )
    page, cursor = service.read(
        tenant_id="tenant",
        session_id="session",
        audience_id="alice",
        traffic_class="transcript",
        since_item_id="m1",
        item_id_field="id",
        queue_limit=3,
        page_limit=100,
    )
    assert [item["id"] for item in page] == ["m2", "m3"]
    assert cursor == "m3"
    assert repository.snapshot()["messages"] == 3
    assert service.clear_session(tenant_id="tenant", session_id="session") == 3
    assert service.clear_session(tenant_id="tenant", session_id="session") == 0


def test_strict_publish_constructs_a_closed_opaque_item() -> None:
    service, _repository = _service()
    envelope = '{"ciphertext_b64":"opaque-ciphertext","payload_type":"pair.chat_message"}'
    assert service.publish_secure_envelope(
        tenant_id="tenant",
        session_id="session",
        epoch=3,
        sender_id="alice",
        audience_id="bob",
        traffic_class="transcript",
        item_id="m-secure",
        item_id_field="id",
        serialized_envelope=envelope,
        queue_limit=10,
    ) == 1
    page, _ = service.read(
        tenant_id="tenant",
        session_id="session",
        audience_id="bob",
        traffic_class="transcript",
        since_item_id="",
        item_id_field="id",
        queue_limit=10,
        page_limit=10,
    )
    assert page == [{"id": "m-secure", "encrypted_payload": envelope}]
    assert "text" not in page[0]
