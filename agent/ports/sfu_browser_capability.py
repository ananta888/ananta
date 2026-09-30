"""Small read/write ports for privacy-bounded browser capability state."""

from __future__ import annotations

from typing import Protocol

from agent.models.sfu_browser_capability import (
    SfuBrowserCapabilitySnapshot,
    SfuBrowserCapabilityWriteResult,
)


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


__all__ = [
    "SfuBrowserCapabilityReadPort",
    "SfuBrowserCapabilityRepositoryPort",
]
