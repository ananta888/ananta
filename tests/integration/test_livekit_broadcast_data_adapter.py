"""Deterministic adapter tests; real runtime evidence remains a separate gate."""

from __future__ import annotations

from dataclasses import replace

from agent.adapters.livekit_broadcast_data_adapter import LivekitBroadcastDataAdapter
from agent.services.sfu_broadcast_data_port import (
    AuthorizedSfuDataAudienceV1,
    EncryptedSfuDataCommandV1,
    SfuDataOutcomeV1,
    SfuDataReasonCodeV1,
    SfuDataReliabilityV1,
)


class Capability:
    def __init__(self, supported: bool) -> None:
        self.supported = supported

    def hub_send_data_supported(self) -> bool:
        return self.supported


class Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish_data(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _command(destination_count: int = 15) -> EncryptedSfuDataCommandV1:
    destinations = tuple(f"receiver-{index:02d}" for index in range(destination_count))
    audience = AuthorizedSfuDataAudienceV1(
        tenant_ref="tenant-a",
        room_ref="room-a",
        publication_ref="publication-a",
        audience_ref="audience-a",
        membership_epoch=2,
        route_epoch=3,
        key_epoch=4,
        fencing_token="fence-a",
        destination_handles=destinations,
        expires_at_ms=2_000,
    )
    return EncryptedSfuDataCommandV1(
        command_id="command-a",
        topic="ananta.sfu-data.v1",
        reliability=SfuDataReliabilityV1.RELIABLE,
        encrypted_packet=b"opaque-ciphertext",
        audience=audience,
        sequence=1,
    )


def test_batches_exact_authorized_destination_set_without_empty_fallback() -> None:
    client = Client()
    adapter = LivekitBroadcastDataAdapter(
        client=client, capability=Capability(True), now_ms=lambda: 1_000
    )

    result = adapter.send(_command())

    assert result.outcome is SfuDataOutcomeV1.ACKNOWLEDGED
    assert result.published_batches == 3
    flattened = tuple(identity for call in client.calls for identity in call["destination_identities"])
    assert flattened == _command().audience.destination_handles
    assert all(call["destination_identities"] for call in client.calls)
    assert all(call["encrypted_packet"] == b"opaque-ciphertext" for call in client.calls)


def test_capability_and_expiry_fail_closed_without_runtime_call() -> None:
    client = Client()
    unsupported = LivekitBroadcastDataAdapter(
        client=client, capability=Capability(False), now_ms=lambda: 1_000
    )
    assert unsupported.send(_command()).reason_code is SfuDataReasonCodeV1.CAPABILITY_UNSUPPORTED
    expired = LivekitBroadcastDataAdapter(
        client=client, capability=Capability(True), now_ms=lambda: 2_000
    )
    assert expired.send(_command()).reason_code is SfuDataReasonCodeV1.EXPIRED
    assert client.calls == []


def test_receipt_is_idempotent_and_conflicting_reuse_is_rejected() -> None:
    client = Client()
    adapter = LivekitBroadcastDataAdapter(
        client=client, capability=Capability(True), now_ms=lambda: 1_000
    )
    command = _command(1)
    first = adapter.send(command)
    duplicate = adapter.send(command)
    conflict = adapter.send(replace(command, encrypted_packet=b"different-ciphertext"))

    assert first.reason_code is SfuDataReasonCodeV1.ACKNOWLEDGED
    assert duplicate.reason_code is SfuDataReasonCodeV1.DUPLICATE_IDEMPOTENT
    assert conflict.reason_code is SfuDataReasonCodeV1.IDEMPOTENCY_CONFLICT
    assert len(client.calls) == 1
