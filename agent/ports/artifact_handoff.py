"""Small ports used by cross-team handoff orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VerifiedArtifactVersion:
    artifact_id: str
    version: str
    digest: str
    verification_status: str
    evidence_refs: tuple[str, ...]
    context_scope_refs: tuple[str, ...]
    producer_task_id: str | None = None


class ArtifactVersionReader(Protocol):
    def get_verified_version(
        self,
        *,
        goal_id: str,
        artifact_id: str,
        version: str,
    ) -> VerifiedArtifactVersion | None: ...


class EvidenceManifestVerifier(Protocol):
    def verify(
        self,
        *,
        evidence_refs: tuple[str, ...],
        context_scope_refs: tuple[str, ...],
        assignment_id: str,
        dispatch_lease_id: str,
    ) -> tuple[bool, tuple[str, ...]]: ...


class HandoffStateStore(Protocol):
    def get(self, handoff_id: str) -> dict | None: ...

    def save_if_revision(self, handoff_id: str, expected_revision: int, value: dict) -> bool: ...


__all__ = [
    "ArtifactVersionReader",
    "EvidenceManifestVerifier",
    "HandoffStateStore",
    "VerifiedArtifactVersion",
]
