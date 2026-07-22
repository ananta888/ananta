"""Persistence-neutral contracts for bounded, content-free TURN accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class TurnAccountingRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, status_code: int = 503) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TurnAccountingCounters:
    allocation_count: int = 0
    active_ports: int = 0
    ingress_bytes: int = 0
    egress_bytes: int = 0
    packet_count: int = 0
    duration_seconds: int = 0
    auth_failures: int = 0
    exhaustion_events: int = 0

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in self.values().values()
        ):
            raise ValueError("turn_accounting_counter_invalid")

    def values(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def delta(self, previous: "TurnAccountingCounters") -> tuple["TurnAccountingCounters", bool]:
        current = self.values()
        before = previous.values()
        if any(current[name] < before[name] for name in current):
            return self, True
        return TurnAccountingCounters(
            **{name: current[name] - before[name] for name in current}
        ), False


@dataclass(frozen=True, slots=True)
class TurnAccountingRecord:
    sequence: int
    observed_at_seconds: int
    window_started_at_seconds: int
    scope_pseudonyms: Mapping[str, str]
    receiver_class: str
    counters: TurnAccountingCounters
    reason_codes: tuple[str, ...]

    def public(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "observed_at_seconds": self.observed_at_seconds,
            "window_started_at_seconds": self.window_started_at_seconds,
            "scope_pseudonyms": dict(self.scope_pseudonyms),
            "receiver_class": self.receiver_class,
            "counters": self.counters.values(),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class TurnAccountingScope:
    tenant_pseudonym: str
    pool_pseudonym: str


@dataclass(frozen=True, slots=True)
class TurnAccountingIngestRequest:
    event_digest: str
    request_digest: str
    source_pseudonym: str
    runtime_epoch_pseudonym: str
    credential_pseudonym: str
    tenant_pseudonym: str
    pool_pseudonym: str
    room_pseudonym: str
    allocation_pseudonym: str
    node_pseudonym: str
    receiver_class: str
    sequence: int
    observed_at_seconds: int
    window_started_at_seconds: int
    counters: TurnAccountingCounters
    late: bool
    retained_until: int
    source_capacity_max: int
    record_capacity_max: int
    now: int


@dataclass(frozen=True, slots=True)
class TurnAccountingRepositoryResult:
    status: str
    record: TurnAccountingRecord


@dataclass(frozen=True, slots=True)
class TurnAccountingPage:
    items: tuple[TurnAccountingRecord, ...]
    next_cursor: str | None


class TurnAccountingRepositoryPort(Protocol):
    def ingest(self, request: TurnAccountingIngestRequest) -> TurnAccountingRepositoryResult: ...

    def page(
        self,
        scope: TurnAccountingScope,
        *,
        cursor: str | None,
        limit: int,
        now: int,
    ) -> TurnAccountingPage: ...

    def purge_expired(self, *, now: int, limit: int) -> int: ...


__all__ = [
    "TurnAccountingCounters",
    "TurnAccountingIngestRequest",
    "TurnAccountingPage",
    "TurnAccountingRecord",
    "TurnAccountingRepositoryError",
    "TurnAccountingRepositoryPort",
    "TurnAccountingRepositoryResult",
    "TurnAccountingScope",
]
