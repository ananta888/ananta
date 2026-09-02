"""Ports and immutable projections for Hub-owned evidence identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceEvidenceIdentity:
    source_id: str
    tenant_id: str
    project_id: str
    origin_type: str
    origin_digest: str
    content_digest: str
    policy_digest: str
    evidence_scope: str
    synthetic: bool
    issuer: str
    state: str
    binding_digest: str
    created_at_epoch: float


@dataclass(frozen=True, slots=True)
class RunEvidenceIdentity:
    run_id: str
    tenant_id: str
    project_id: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    repository_revision: str
    input_digest: str
    execution_profile_digest: str
    environment_digest: str
    source_ids: tuple[str, ...]
    evidence_scope: str
    synthetic: bool
    issuer: str
    reservation_key_digest: str
    binding_digest: str
    state: str
    result_digest: str | None
    created_at_epoch: float
    updated_at_epoch: float


class EvidenceIdentityRepositoryPort(Protocol):
    def register_source(
        self, identity: SourceEvidenceIdentity
    ) -> SourceEvidenceIdentity: ...

    def get_source(
        self, *, tenant_id: str, project_id: str, source_id: str
    ) -> SourceEvidenceIdentity | None: ...

    def reserve_run(
        self, identity: RunEvidenceIdentity
    ) -> RunEvidenceIdentity: ...

    def get_run(
        self, *, tenant_id: str, project_id: str, run_id: str
    ) -> RunEvidenceIdentity | None: ...

    def complete_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        terminal_state: str,
        result_digest: str,
        updated_at_epoch: float,
    ) -> RunEvidenceIdentity: ...


__all__ = [
    "EvidenceIdentityRepositoryPort",
    "RunEvidenceIdentity",
    "SourceEvidenceIdentity",
]
