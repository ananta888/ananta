"""Small read/write ports for privacy-bounded browser capability state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Protocol


CapabilityState = Literal["active", "unknown", "unsupported", "stale"]


@dataclass(frozen=True, slots=True)
class SfuBrowserCapabilitySnapshot:
    tenant_id: str
    room_id: str
    browser_pseudonym: str
    admission_epoch: int
    membership_epoch: int
    sequence: int
    version: int
    capability_version: str
    capability_class: str
    buckets: tuple[Mapping[str, str], ...]
    state: CapabilityState
    expires_at_ms: int
    document_digest: str


@dataclass(frozen=True, slots=True)
class SfuBrowserCapabilityWriteResult:
    status: Literal["saved", "replayed", "conflict", "capacity"]
    snapshot: SfuBrowserCapabilitySnapshot | None
    reason_code: str


class SfuBrowserCapabilityReadPort(Protocol):
    def read(
        self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
        admission_epoch: int, membership_epoch: int, now_ms: int,
    ) -> SfuBrowserCapabilitySnapshot: ...


class SfuBrowserCapabilityRepositoryPort(SfuBrowserCapabilityReadPort, Protocol):
    def save(
        self, snapshot: SfuBrowserCapabilitySnapshot, *, expected_version: int,
        room_cardinality_max: int, now_ms: int,
    ) -> SfuBrowserCapabilityWriteResult: ...

    def revoke(
        self, *, tenant_id: str, room_id: str, browser_pseudonym: str,
        expected_version: int, now_ms: int,
    ) -> SfuBrowserCapabilityWriteResult: ...

    def purge(self, *, now_ms: int, limit: int) -> int: ...


def unknown_capability(
    *, tenant_id: str, room_id: str, browser_pseudonym: str,
    admission_epoch: int, membership_epoch: int,
) -> SfuBrowserCapabilitySnapshot:
    return SfuBrowserCapabilitySnapshot(
        tenant_id, room_id, browser_pseudonym, admission_epoch, membership_epoch,
        0, 0, "unknown", "unknown", (), "unknown", 0, "",
    )


__all__ = [
    "CapabilityState", "SfuBrowserCapabilityReadPort",
    "SfuBrowserCapabilityRepositoryPort", "SfuBrowserCapabilitySnapshot",
    "SfuBrowserCapabilityWriteResult", "unknown_capability",
]
