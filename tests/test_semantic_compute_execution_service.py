from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.semantic_compute_candidate_repository import (
    ConservativeCandidateObservation,
    SemanticComputeCandidateRepository,
    SemanticComputeCandidateRepositoryError,
)
from agent.repositories.semantic_compute_schedule_repository import (
    SemanticComputeScheduleRepository,
)
from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticPrincipal,
)
from agent.repositories.semantic_lease_repository import SemanticLeaseRepository
from agent.services.semantic_compute_execution_service import (
    SemanticComputeExecutionError,
    SemanticComputeExecutionService,
)
from agent.services.semantic_task_lease_authority import HubSemanticTaskLeaseAuthority
from ananta_contracts.semantic_compute import canonical_json
from tests.semantic_compute_support import capability, compute_contract


class _ConsentAuthority:
    def __init__(self) -> None:
        self.revoked: set[str] = set()

    def authorized(self, context) -> bool:
        return context.candidate_id not in self.revoked


def _stack():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    now = [1_000.0]
    contracts = SemanticContractRepository(db_engine=engine, clock=lambda: now[0])
    candidates = SemanticComputeCandidateRepository(
        db_engine=engine,
        clock=lambda: now[0],
        observation=ConservativeCandidateObservation(maximum_capacity=1),
    )
    leases = SemanticLeaseRepository(db_engine=engine, clock=lambda: now[0], clock_skew_seconds=0)
    consent = _ConsentAuthority()
    lease_authority = HubSemanticTaskLeaseAuthority(
        b"semantic-compute-execution-test-key" * 2,
        clock_ms=lambda: int(now[0] * 1_000),
    )
    service = SemanticComputeExecutionService(
        contracts=contracts,
        candidates=candidates,
        leases=leases,
        receipts=SemanticComputeScheduleRepository(db_engine=engine, clock=lambda: now[0]),
        clock=lambda: now[0],
        feature_enabled=lambda: True,
        security_confirmed=lambda: True,
        consent_authority=consent,
        lease_authority=lease_authority,
    )
    return now, contracts, candidates, leases, consent, service


def _member(contracts, principal, *, role="participant"):
    contracts.put_membership(
        principal,
        session_id="session-a",
        room_id="room-a",
        epoch=1,
        role=role,
        permissions={"semantic_compute": True},
        expires_at=2_000,
    )


def _signed_capability(private_key: Ed25519PrivateKey, *, sender="peer-a"):
    value = capability(now_ms=1_000_000, sender_id=sender)
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = f"cap-{hashlib.sha256(public).hexdigest()[:32]}"
    unsigned = {name: item for name, item in value.items() if name != "signature"}
    value["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "value": base64.b64encode(private_key.sign(canonical_json(unsigned))).decode(),
    }
    return value, key_id, base64.b64encode(public).decode()


def _active_contract(contracts, owner):
    payload = compute_contract(now_ms=1_000_000)
    contracts.create(
        owner,
        contract_id=payload["contract_id"],
        request_digest="a" * 64,
        idempotency_key="active-contract-create",
        payload=payload,
        status="active",
    )
    return payload


def test_key_binding_rejects_substitution_and_unbound_advertisement() -> None:
    _now, contracts, candidates, _leases, _consent, service = _stack()
    peer = SemanticPrincipal("tenant-a", "peer-a")
    _member(contracts, peer)
    private = Ed25519PrivateKey.generate()
    advertisement, key_id, public = _signed_capability(private)

    with pytest.raises(SemanticComputeExecutionError, match="candidate_key_not_bound"):
        service.advertise_candidate(peer, advertisement=advertisement)
    service.register_candidate_key(
        peer,
        session_id="session-a",
        epoch=1,
        key_id=key_id,
        public_key_b64=public,
        expires_at_ms=1_060_000,
    )
    accepted = service.advertise_candidate(peer, advertisement=advertisement)
    assert accepted["scheduler_authority"] is False
    forged = dict(advertisement)
    forged["max_delay_ms"] = 20_000
    with pytest.raises(SemanticComputeCandidateRepositoryError, match="candidate_signature_invalid"):
        candidates.put_advertisement(peer, raw=forged)


def test_active_contract_schedules_from_persisted_advertisement_and_replays() -> None:
    _now, contracts, _candidates, _leases, consent, service = _stack()
    owner = SemanticPrincipal("tenant-a", "owner-a")
    peer = SemanticPrincipal("tenant-a", "peer-a")
    _member(contracts, owner, role="owner")
    _member(contracts, peer)
    payload = _active_contract(contracts, owner)
    private = Ed25519PrivateKey.generate()
    advertisement, key_id, public = _signed_capability(private)
    service.register_candidate_key(
        peer,
        session_id="session-a",
        epoch=1,
        key_id=key_id,
        public_key_b64=public,
        expires_at_ms=1_060_000,
    )
    service.advertise_candidate(peer, advertisement=advertisement)
    values = dict(
        contract_id=payload["contract_id"],
        session_id="session-a",
        epoch=1,
        expected_revision=1,
        task_type="visual_extract",
        audience="owner-a",
        sequence_start=0,
        sequence_end=9,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        deadline_epoch_ms=1_004_000,
        validator_count=0,
        hot_standby=False,
        idempotency_key="schedule-exactly-once",
    )
    first = service.schedule(owner, **values)
    second = service.schedule(owner, **values)
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["leases"][0]["lease_id"] == second["leases"][0]["lease_id"]
    assert first["leases"][0]["executor_id"] == "peer-a"
    assert first["leases"][0]["task_lease"]["issuer"] == "hub"
    assert set(first["leases"][0]["task_lease"]["signature"]) == {
        "algorithm",
        "key_id",
        "value",
    }
    consent.revoked.add("peer-a")
    with pytest.raises(SemanticComputeExecutionError, match="no_eligible_primary"):
        service.schedule(
            owner,
            **{
                **values,
                "sequence_start": 10,
                "sequence_end": 19,
                "idempotency_key": "schedule-after-consent-revoke",
            },
        )


def test_persisted_active_assignments_balance_successive_non_overlapping_tasks() -> None:
    _now, contracts, _candidates, _leases, _consent, service = _stack()
    owner = SemanticPrincipal("tenant-a", "owner-a")
    _member(contracts, owner, role="owner")
    payload = _active_contract(contracts, owner)
    for sender in ("peer-a", "peer-b"):
        peer = SemanticPrincipal("tenant-a", sender)
        _member(contracts, peer)
        private = Ed25519PrivateKey.generate()
        advertisement, key_id, public = _signed_capability(private, sender=sender)
        service.register_candidate_key(
            peer,
            session_id="session-a",
            epoch=1,
            key_id=key_id,
            public_key_b64=public,
            expires_at_ms=1_060_000,
        )
        service.advertise_candidate(peer, advertisement=advertisement)

    common = dict(
        contract_id=payload["contract_id"],
        session_id="session-a",
        epoch=1,
        expected_revision=1,
        task_type="visual_extract",
        audience="owner-a",
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        deadline_epoch_ms=1_004_000,
        validator_count=0,
        hot_standby=False,
    )
    first = service.schedule(
        owner,
        **common,
        sequence_start=0,
        sequence_end=9,
        idempotency_key="fairness-first",
    )
    second = service.schedule(
        owner,
        **common,
        sequence_start=10,
        sequence_end=19,
        idempotency_key="fairness-second",
    )
    assert first["leases"][0]["executor_id"] == "peer-a"
    assert second["leases"][0]["executor_id"] == "peer-b"


def test_lease_reduce_and_revoke_are_cas_idempotent_and_never_expand() -> None:
    _now, contracts, _candidates, leases, _consent, service = _stack()
    owner = SemanticPrincipal("tenant-a", "owner-a")
    peer = SemanticPrincipal("tenant-a", "peer-a")
    _member(contracts, owner, role="owner")
    _member(contracts, peer)
    payload = _active_contract(contracts, owner)
    private = Ed25519PrivateKey.generate()
    advertisement, key_id, public = _signed_capability(private)
    service.register_candidate_key(
        peer,
        session_id="session-a",
        epoch=1,
        key_id=key_id,
        public_key_b64=public,
        expires_at_ms=1_060_000,
    )
    service.advertise_candidate(peer, advertisement=advertisement)
    scheduled = service.schedule(
        owner,
        contract_id=payload["contract_id"],
        session_id="session-a",
        epoch=1,
        expected_revision=1,
        task_type="visual_extract",
        audience="owner-a",
        sequence_start=0,
        sequence_end=9,
        resource_budget={"cpu_ms": 100, "memory_bytes": 1_048_576, "artifact_bytes": 1_024},
        deadline_epoch_ms=1_004_000,
        validator_count=0,
        hot_standby=False,
        idempotency_key="schedule-for-cas",
    )
    lease = scheduled["leases"][0]
    reduced = service.reduce_lease(
        owner,
        lease_id=lease["lease_id"],
        session_id="session-a",
        epoch=1,
        expected_version=lease["version"],
        fencing_token=lease["fencing_token"],
        resource_budget={"cpu_ms": 50, "memory_bytes": 524_288, "artifact_bytes": 512},
        expires_at_ms=1_003_000,
        idempotency_key="reduce-exactly-once",
    )
    replay = service.reduce_lease(
        owner,
        lease_id=lease["lease_id"],
        session_id="session-a",
        epoch=1,
        expected_version=lease["version"],
        fencing_token=lease["fencing_token"],
        resource_budget={"cpu_ms": 50, "memory_bytes": 524_288, "artifact_bytes": 512},
        expires_at_ms=1_003_000,
        idempotency_key="reduce-exactly-once",
    )
    assert reduced["version"] == 2 and replay["idempotent_replay"] is True
    with pytest.raises(SemanticComputeExecutionError, match="lease_expansion_forbidden"):
        service.reduce_lease(
            owner,
            lease_id=lease["lease_id"],
            session_id="session-a",
            epoch=1,
            expected_version=2,
            fencing_token=lease["fencing_token"],
            resource_budget={"cpu_ms": 101, "memory_bytes": 524_288, "artifact_bytes": 512},
            expires_at_ms=None,
            idempotency_key="expand-forbidden",
        )
    revoked = service.revoke_lease(
        owner,
        lease_id=lease["lease_id"],
        session_id="session-a",
        epoch=1,
        expected_version=2,
        fencing_token=lease["fencing_token"],
        idempotency_key="revoke-exactly-once",
    )
    assert revoked["status"] == "revoked"
    with pytest.raises(Exception):
        leases.authorize_result(
            lease_id=lease["lease_id"],
            contract_digest=payload["contract_digest"],
            fencing_token=lease["fencing_token"],
            session_id="session-a",
            epoch=1,
            task_type="visual_extract",
            audience="owner-a",
        )
