from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticPrincipal,
)
from agent.repositories.semantic_lease_repository import LeaseRequest, SemanticLeaseRepository
from agent.services.semantic_contract_service import HubContractSigner, SemanticContractService
from tests.semantic_compute_support import capability


def test_every_contract_revision_revokes_old_digest_leases_before_exposure() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    now = 1_000.0
    repository = SemanticContractRepository(db_engine=engine, clock=lambda: now)
    leases = SemanticLeaseRepository(db_engine=engine, clock=lambda: now, clock_skew_seconds=0)
    service = SemanticContractService(
        repository,
        signer=HubContractSigner(b"s" * 32),
        clock=lambda: now,
        feature_enabled=lambda: True,
        lease_revoker=leases,
    )
    principal = SemanticPrincipal("tenant-a", "owner-a")
    repository.put_membership(
        principal,
        session_id="session-a",
        room_id="room-a",
        epoch=1,
        role="owner",
        permissions={"semantic_compute": True},
        expires_at=2_000,
    )
    advertisement = capability(now_ms=1_000_000, sender_id="owner-a")
    proposal = {
        "profile": "balanced",
        "quality_level": "standard",
        "delay_ms": 5_000,
        "security_mode": "strict_e2ee",
        "trusted_compute_grant": False,
        "task_types": ["visual_extract"],
        "max_artifact_bytes": 65_536,
        "deadline_ms": 5_000,
        "expires_at_ms": 1_300_000,
    }
    offered = service.create_offer(
        principal,
        session_id="session-a",
        room_id="room-a",
        epoch=1,
        policy_version="policy-v1",
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
        proposal=proposal,
        advertisements=[advertisement],
        idempotency_key="lease-revoke-offer",
    )
    accepted = service.mutate(
        principal,
        contract_id=offered["contract_id"],
        session_id="session-a",
        epoch=1,
        action="accept",
        expected_revision=1,
        idempotency_key="lease-revoke-accept",
        proposal={},
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
        advertisements=[advertisement],
    )
    active = service.mutate(
        principal,
        contract_id=offered["contract_id"],
        session_id="session-a",
        epoch=1,
        action="activate",
        expected_revision=accepted["revision"],
        idempotency_key="lease-revoke-activate",
        proposal={},
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
        advertisements=[advertisement],
    )
    lease = leases.acquire(
        LeaseRequest(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            contract_id=active["contract_id"],
            contract_digest=active["digest"],
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="owner-a",
            role="primary",
            executor_id="owner-a",
            sequence_start=0,
            sequence_end=1,
            resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
            ttl_seconds=30,
            deadline_at=1_004,
        )
    )

    countered = service.mutate(
        principal,
        contract_id=active["contract_id"],
        session_id="session-a",
        epoch=1,
        action="counter",
        expected_revision=active["revision"],
        idempotency_key="lease-revoke-counter",
        proposal={"profile": "conservative"},
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
    )

    assert countered["digest"] != active["digest"]
    assert leases.get(lease.id).status == "revoked"
