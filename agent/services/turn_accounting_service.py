"""Stateless validation and pseudonymization for durable TURN accounting."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from agent.services.sfu_broadcast_control_observability import (
    SfuBroadcastControlObservationPort,
    control_observer_or_null,
    observed_control_path,
)
from agent.services.turn_accounting_repository_port import (
    TurnAccountingCounters,
    TurnAccountingIngestRequest,
    TurnAccountingPage,
    TurnAccountingRecord,
    TurnAccountingRepositoryError,
    TurnAccountingRepositoryPort,
    TurnAccountingScope,
)


class TurnAccountingError(ValueError):
    def __init__(self, reason_code: str, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnAccountingEvent:
    event_id: str
    credential_id: str
    tenant_ref: str
    turn_pool_ref: str
    room_ref: str
    allocation_ref: str
    receiver_class: str
    sfu_node_ref: str
    turn_runtime_epoch: str
    sequence: int
    observed_at_seconds: int
    window_started_at_seconds: int
    counters: TurnAccountingCounters


@dataclass(frozen=True, slots=True)
class TurnAccountingIngestResult:
    accepted: bool
    replayed: bool
    record: TurnAccountingRecord


class TurnAccountingService:
    RECEIVER_CLASSES = frozenset(
        {"direct_capable", "relay_required", "relay_preferred", "unknown"}
    )

    def __init__(
        self,
        repository: TurnAccountingRepositoryPort,
        *,
        pseudonym_secret: bytes,
        window_seconds: int = 60,
        retention_seconds: int = 86_400,
        late_window_seconds: int = 300,
        max_sources: int = 4096,
        max_records: int = 262_144,
        clock: Callable[[], float] = time.time,
        control_observer: SfuBroadcastControlObservationPort | None = None,
    ) -> None:
        if (
            len(pseudonym_secret) < 32
            or not 10 <= window_seconds <= 3600
            or not window_seconds <= late_window_seconds <= 86_400
            or not late_window_seconds + window_seconds <= retention_seconds <= 2_592_000
            or not 1 <= max_sources <= 100_000
            or not max_sources <= max_records <= 10_000_000
        ):
            raise TurnAccountingError("turn_accounting_configuration_invalid", 503)
        self._repository = repository
        self._secret = bytes(pseudonym_secret)
        self._window = window_seconds
        self._retention = retention_seconds
        self._late = late_window_seconds
        self._max_sources = max_sources
        self._max_records = max_records
        self._clock = clock
        self._control_observer = control_observer_or_null(control_observer)

    @observed_control_path("turn_ingestion")
    def ingest(self, event: TurnAccountingEvent) -> TurnAccountingIngestResult:
        self._validate(event)
        now = int(self._clock())
        if event.observed_at_seconds > now + 30:
            raise TurnAccountingError("turn_accounting_clock_skew")
        if now - event.observed_at_seconds >= self._retention:
            raise TurnAccountingError("turn_accounting_event_expired", 410)
        pseudonyms = {
            "credential": self._digest("credential", event.credential_id)[:24],
            "tenant": self._digest("tenant", event.tenant_ref)[:24],
            "pool": self._digest("pool", event.turn_pool_ref)[:24],
            "room": self._digest("room", event.room_ref)[:24],
            "allocation": self._digest("allocation", event.allocation_ref)[:24],
            "node": self._digest("node", event.sfu_node_ref)[:24],
        }
        source = self._digest(
            "source",
            "\0".join(
                (
                    event.tenant_ref,
                    event.turn_pool_ref,
                    event.allocation_ref,
                    event.sfu_node_ref,
                )
            ),
        )
        runtime = self._digest("runtime", event.turn_runtime_epoch)[:24]
        event_digest = self._digest("event", event.event_id)
        request_digest = hashlib.sha256(
            json.dumps(
                {
                    "event": event_digest,
                    "source": source,
                    "runtime": runtime,
                    "scope": pseudonyms,
                    "receiver_class": event.receiver_class,
                    "sequence": event.sequence,
                    "observed": event.observed_at_seconds,
                    "window": event.window_started_at_seconds,
                    "counters": event.counters.values(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
        request = TurnAccountingIngestRequest(
            event_digest=event_digest,
            request_digest=request_digest,
            source_pseudonym=source,
            runtime_epoch_pseudonym=runtime,
            credential_pseudonym=pseudonyms["credential"],
            tenant_pseudonym=pseudonyms["tenant"],
            pool_pseudonym=pseudonyms["pool"],
            room_pseudonym=pseudonyms["room"],
            allocation_pseudonym=pseudonyms["allocation"],
            node_pseudonym=pseudonyms["node"],
            receiver_class=event.receiver_class,
            sequence=event.sequence,
            observed_at_seconds=event.observed_at_seconds,
            window_started_at_seconds=event.window_started_at_seconds,
            counters=event.counters,
            late=now - event.window_started_at_seconds > self._late,
            retained_until=now + self._retention,
            source_capacity_max=self._max_sources,
            record_capacity_max=self._max_records,
            now=now,
        )
        try:
            outcome = self._repository.ingest(request)
        except TurnAccountingRepositoryError as exc:
            raise TurnAccountingError(exc.reason_code, exc.status_code) from exc
        return TurnAccountingIngestResult(
            outcome.status == "accepted",
            outcome.status == "replayed",
            outcome.record,
        )

    def page(
        self,
        *,
        tenant_ref: str,
        turn_pool_ref: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> TurnAccountingPage:
        self._validate_identifier(tenant_ref)
        self._validate_identifier(turn_pool_ref)
        try:
            return self._repository.page(
                TurnAccountingScope(
                    self._digest("tenant", tenant_ref)[:24],
                    self._digest("pool", turn_pool_ref)[:24],
                ),
                cursor=cursor,
                limit=limit,
                now=int(self._clock()),
            )
        except TurnAccountingRepositoryError as exc:
            raise TurnAccountingError(exc.reason_code, exc.status_code) from exc

    def purge_expired(self, *, limit: int = 200) -> int:
        try:
            return self._repository.purge_expired(now=int(self._clock()), limit=limit)
        except TurnAccountingRepositoryError as exc:
            raise TurnAccountingError(exc.reason_code, exc.status_code) from exc

    @observed_control_path("turn_failover")
    def reconcile_sfu_egress(
        self,
        *,
        turn_egress_bytes: int,
        sfu_egress_bytes: int,
        tolerance_bytes: int,
    ) -> str:
        values = (turn_egress_bytes, sfu_egress_bytes, tolerance_bytes)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise TurnAccountingError("turn_accounting_reconciliation_input_invalid")
        if abs(turn_egress_bytes - sfu_egress_bytes) > tolerance_bytes:
            return "turn_accounting_sfu_egress_reconciliation_required"
        return "turn_accounting_sfu_egress_reconciled"

    def _validate(self, event: TurnAccountingEvent) -> None:
        if not isinstance(event, TurnAccountingEvent):
            raise TurnAccountingError("turn_accounting_event_invalid")
        for value in (
            event.event_id,
            event.credential_id,
            event.tenant_ref,
            event.turn_pool_ref,
            event.room_ref,
            event.allocation_ref,
            event.sfu_node_ref,
            event.turn_runtime_epoch,
        ):
            self._validate_identifier(value)
        if event.receiver_class not in self.RECEIVER_CLASSES:
            raise TurnAccountingError("turn_accounting_receiver_class_invalid")
        for value in (
            event.sequence,
            event.observed_at_seconds,
            event.window_started_at_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TurnAccountingError("turn_accounting_event_invalid")
        if (
            event.sequence < 1
            or event.window_started_at_seconds % self._window
            or not event.window_started_at_seconds
            <= event.observed_at_seconds
            < event.window_started_at_seconds + self._window
        ):
            raise TurnAccountingError("turn_accounting_window_invalid")

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value
        ):
            raise TurnAccountingError("turn_accounting_scope_invalid")

    def _digest(self, domain: str, value: str) -> str:
        return hmac.new(
            self._secret,
            f"turn-accounting-{domain}-v1\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()


__all__ = [
    "TurnAccountingCounters",
    "TurnAccountingError",
    "TurnAccountingEvent",
    "TurnAccountingIngestResult",
    "TurnAccountingPage",
    "TurnAccountingRecord",
    "TurnAccountingRepositoryPort",
    "TurnAccountingService",
]
