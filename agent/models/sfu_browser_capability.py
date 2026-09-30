"""Privacy-bounded browser capability state and its admission scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping


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


def unknown_capability(
    *, tenant_id: str, room_id: str, browser_pseudonym: str,
    admission_epoch: int, membership_epoch: int,
) -> SfuBrowserCapabilitySnapshot:
    return SfuBrowserCapabilitySnapshot(
        tenant_id, room_id, browser_pseudonym, admission_epoch, membership_epoch,
        0, 0, "unknown", "unknown", (), "unknown", 0, "",
    )


@dataclass(frozen=True, slots=True)
class SfuCapabilityAdmissionScope:
    tenant_id: str
    room_id: str
    actor_id: str
    admission_epoch: int
    membership_epoch: int


__all__ = [
    "CapabilityState",
    "SfuBrowserCapabilitySnapshot",
    "SfuBrowserCapabilityWriteResult",
    "SfuCapabilityAdmissionScope",
    "unknown_capability",
]
