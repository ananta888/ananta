"""Small payload-blind port for bounded encrypted SFU data fanout."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class SfuDataReliabilityV1(StrEnum):
    RELIABLE = "reliable"
    LOSSY = "lossy"


class SfuDataOutcomeV1(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class SfuDataReasonCodeV1(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    DUPLICATE_IDEMPOTENT = "duplicate_idempotent"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    EXPIRED = "expired"
    BACKPRESSURE = "backpressure"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


@dataclass(frozen=True, slots=True)
class SfuBroadcastDataLimitsV1:
    reliable_packet_bytes_max: int = 14_336
    lossy_packet_bytes_max: int = 1_300
    destination_identities_per_publish_max: int = 7
    batch_count_max: int = 36
    send_attempts_max: int = 1
    concurrent_messages_max: int = 1


DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1 = SfuBroadcastDataLimitsV1()


@dataclass(frozen=True, slots=True)
class AuthorizedSfuDataAudienceV1:
    tenant_ref: str
    room_ref: str
    publication_ref: str
    audience_ref: str
    membership_epoch: int
    route_epoch: int
    key_epoch: int
    fencing_token: str
    destination_handles: tuple[str, ...]
    expires_at_ms: int

    def __post_init__(self) -> None:
        identifiers = (
            self.tenant_ref,
            self.room_ref,
            self.publication_ref,
            self.audience_ref,
            self.fencing_token,
            *self.destination_handles,
        )
        if any(not _identifier(value) for value in identifiers):
            raise ValueError("sfu_data_audience_identifier_invalid")
        if any(type(value) is not int or value < 1 for value in (
            self.membership_epoch,
            self.route_epoch,
            self.key_epoch,
            self.expires_at_ms,
        )):
            raise ValueError("sfu_data_audience_epoch_invalid")
        if not self.destination_handles or tuple(sorted(set(self.destination_handles))) != self.destination_handles:
            raise ValueError("sfu_data_audience_not_canonical")
        if len(self.destination_handles) > (
            DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1.destination_identities_per_publish_max
            * DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1.batch_count_max
        ):
            raise ValueError("sfu_data_audience_too_large")


@dataclass(frozen=True, slots=True)
class EncryptedSfuDataCommandV1:
    command_id: str
    topic: str
    reliability: SfuDataReliabilityV1
    encrypted_packet: bytes
    audience: AuthorizedSfuDataAudienceV1
    sequence: int

    def __post_init__(self) -> None:
        if not _identifier(self.command_id) or self.topic != "ananta.sfu-data.v1":
            raise ValueError("sfu_data_command_invalid")
        if not isinstance(self.encrypted_packet, bytes) or not self.encrypted_packet:
            raise ValueError("sfu_data_encrypted_packet_invalid")
        limit = (
            DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1.reliable_packet_bytes_max
            if self.reliability is SfuDataReliabilityV1.RELIABLE
            else DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1.lossy_packet_bytes_max
        )
        if len(self.encrypted_packet) > limit:
            raise ValueError("sfu_data_encrypted_packet_oversize")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("sfu_data_sequence_invalid")


@dataclass(frozen=True, slots=True)
class SfuDataPublishResultV1:
    command_id: str
    outcome: SfuDataOutcomeV1
    reason_code: SfuDataReasonCodeV1
    published_batches: int
    retryable: bool


@runtime_checkable
class SfuBroadcastDataPortV1(Protocol):
    def send(self, command: EncryptedSfuDataCommandV1) -> SfuDataPublishResultV1: ...


@runtime_checkable
class SfuBroadcastDataCapabilityPortV1(Protocol):
    def hub_send_data_supported(self) -> bool: ...


def _identifier(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return False
    return value[0].isalnum() and all(character.isalnum() or character in "._:-" for character in value)


__all__ = [
    "AuthorizedSfuDataAudienceV1",
    "DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1",
    "EncryptedSfuDataCommandV1",
    "SfuBroadcastDataCapabilityPortV1",
    "SfuBroadcastDataLimitsV1",
    "SfuBroadcastDataPortV1",
    "SfuDataOutcomeV1",
    "SfuDataPublishResultV1",
    "SfuDataReasonCodeV1",
    "SfuDataReliabilityV1",
]
