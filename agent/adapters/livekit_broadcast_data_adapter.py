"""Capability-gated LiveKit boundary for already encrypted data packets."""

from __future__ import annotations

from collections import OrderedDict
from hashlib import sha256
from threading import Lock
from time import time
from typing import Callable, Protocol

from agent.services.sfu_broadcast_data_port import (
    DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1,
    EncryptedSfuDataCommandV1,
    SfuBroadcastDataCapabilityPortV1,
    SfuBroadcastDataLimitsV1,
    SfuDataOutcomeV1,
    SfuDataPublishResultV1,
    SfuDataReasonCodeV1,
    SfuDataReliabilityV1,
)


class LivekitEncryptedDataClientV1(Protocol):
    def publish_data(
        self,
        *,
        room_ref: str,
        encrypted_packet: bytes,
        reliable: bool,
        topic: str,
        destination_identities: tuple[str, ...],
    ) -> None: ...


class LivekitBroadcastDataAdapter:
    """Never receives a content key or a plaintext-shaped argument."""

    def __init__(
        self,
        *,
        client: LivekitEncryptedDataClientV1,
        capability: SfuBroadcastDataCapabilityPortV1,
        limits: SfuBroadcastDataLimitsV1 = DEFAULT_SFU_BROADCAST_DATA_LIMITS_V1,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._client = client
        self._capability = capability
        self._limits = limits
        self._now_ms = now_ms or (lambda: int(time() * 1000))
        self._lock = Lock()
        self._receipts: OrderedDict[str, tuple[str, SfuDataPublishResultV1]] = OrderedDict()
        self._receipt_limit = 256

    def send(self, command: EncryptedSfuDataCommandV1) -> SfuDataPublishResultV1:
        digest = self._digest(command)
        existing = self._receipts.get(command.command_id)
        if existing is not None:
            if existing[0] != digest:
                return self._result(command, SfuDataOutcomeV1.REJECTED, SfuDataReasonCodeV1.IDEMPOTENCY_CONFLICT)
            prior = existing[1]
            return SfuDataPublishResultV1(
                command_id=command.command_id,
                outcome=prior.outcome,
                reason_code=SfuDataReasonCodeV1.DUPLICATE_IDEMPOTENT,
                published_batches=prior.published_batches,
                retryable=False,
            )
        if not self._capability.hub_send_data_supported():
            return self._result(command, SfuDataOutcomeV1.REJECTED, SfuDataReasonCodeV1.CAPABILITY_UNSUPPORTED)
        if command.audience.expires_at_ms <= self._now_ms():
            return self._result(command, SfuDataOutcomeV1.REJECTED, SfuDataReasonCodeV1.EXPIRED)
        if not self._lock.acquire(blocking=False):
            return self._result(command, SfuDataOutcomeV1.REJECTED, SfuDataReasonCodeV1.BACKPRESSURE)
        published = 0
        try:
            destinations = command.audience.destination_handles
            batches = tuple(
                destinations[offset:offset + self._limits.destination_identities_per_publish_max]
                for offset in range(0, len(destinations), self._limits.destination_identities_per_publish_max)
            )
            if not batches or len(batches) > self._limits.batch_count_max:
                return self._result(command, SfuDataOutcomeV1.REJECTED, SfuDataReasonCodeV1.BACKPRESSURE)
            for batch in batches:
                # send_attempts_max is intentionally one: ambiguous retries are reconciled by command receipt.
                self._client.publish_data(
                    room_ref=command.audience.room_ref,
                    encrypted_packet=bytes(command.encrypted_packet),
                    reliable=command.reliability is SfuDataReliabilityV1.RELIABLE,
                    topic=command.topic,
                    destination_identities=batch,
                )
                published += 1
            result = SfuDataPublishResultV1(
                command_id=command.command_id,
                outcome=SfuDataOutcomeV1.ACKNOWLEDGED,
                reason_code=SfuDataReasonCodeV1.ACKNOWLEDGED,
                published_batches=published,
                retryable=False,
            )
            self._remember(command.command_id, digest, result)
            return result
        except Exception:
            return SfuDataPublishResultV1(
                command_id=command.command_id,
                outcome=SfuDataOutcomeV1.UNKNOWN,
                reason_code=SfuDataReasonCodeV1.RUNTIME_UNAVAILABLE,
                published_batches=published,
                retryable=False,
            )
        finally:
            self._lock.release()

    def _remember(self, command_id: str, digest: str, result: SfuDataPublishResultV1) -> None:
        self._receipts[command_id] = (digest, result)
        self._receipts.move_to_end(command_id)
        while len(self._receipts) > self._receipt_limit:
            self._receipts.popitem(last=False)

    @staticmethod
    def _digest(command: EncryptedSfuDataCommandV1) -> str:
        binding = "\x1f".join((
            command.topic,
            command.reliability.value,
            command.audience.tenant_ref,
            command.audience.room_ref,
            command.audience.publication_ref,
            command.audience.audience_ref,
            str(command.audience.membership_epoch),
            str(command.audience.route_epoch),
            str(command.audience.key_epoch),
            command.audience.fencing_token,
            *command.audience.destination_handles,
            str(command.sequence),
        )).encode("utf-8")
        return sha256(binding + b"\0" + command.encrypted_packet).hexdigest()

    @staticmethod
    def _result(
        command: EncryptedSfuDataCommandV1,
        outcome: SfuDataOutcomeV1,
        reason: SfuDataReasonCodeV1,
    ) -> SfuDataPublishResultV1:
        return SfuDataPublishResultV1(command.command_id, outcome, reason, 0, False)


__all__ = ["LivekitBroadcastDataAdapter", "LivekitEncryptedDataClientV1"]
