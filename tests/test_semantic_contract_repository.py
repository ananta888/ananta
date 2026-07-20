from __future__ import annotations

import hashlib
import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.semantic_contract_repository import (
    ContractMutation,
    SemanticContractRepository,
    SemanticContractRepositoryError,
    SemanticPrincipal,
)
from ananta_contracts.semantic_compute import canonical_json
from tests.semantic_compute_support import compute_contract


def repository():
    db = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(db)
    return SemanticContractRepository(db_engine=db, clock=lambda: 1_000.0), db


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def member(repo, principal, session_id="session-a"):
    repo.put_membership(
        principal, session_id=session_id, epoch=1, role="owner",
        permissions={"semantic_compute": True}, expires_at=2_000,
    )


def test_create_replay_cas_activate_and_revoke_are_persistent() -> None:
    repo, db = repository()
    principal = SemanticPrincipal("tenant-a", "owner-a")
    member(repo, principal)
    payload = compute_contract()
    item, replayed = repo.create(
        principal, contract_id=payload["contract_id"], request_digest=digest(payload),
        idempotency_key="idempotency-create", payload=payload, status="offered",
    )
    assert not replayed and item.revision == 1
    replay, replayed = repo.create(
        principal, contract_id=payload["contract_id"], request_digest=digest(payload),
        idempotency_key="idempotency-create", payload=payload, status="offered",
    )
    assert replayed and replay.id == item.id

    active_payload = compute_contract(revision=2)
    active, _ = repo.mutate(
        principal,
        contract_id=item.id,
        mutation=ContractMutation(
            "activate", "idempotency-active", digest(active_payload), 1, item.digest,
            active_payload, "active", True,
        ),
    )
    assert active.status == "active" and active.active_scope_key
    original_replay, replayed = repo.create(
        principal, contract_id=payload["contract_id"], request_digest=digest(payload),
        idempotency_key="idempotency-create", payload=payload, status="offered",
    )
    assert replayed and original_replay.revision == 1 and original_replay.status == "offered"
    with pytest.raises(SemanticContractRepositoryError, match="stale_revision"):
        repo.mutate(
            principal, contract_id=item.id,
            mutation=ContractMutation(
                "counter", "idempotency-stale", digest(active_payload), 1, item.digest,
                active_payload, "countered",
            ),
        )
    revoked_payload = compute_contract(revision=3, profile="off")
    revoked, _ = SemanticContractRepository(db_engine=db, clock=lambda: 1_001.0).mutate(
        principal, contract_id=item.id,
        mutation=ContractMutation(
            "revoke", "idempotency-revoke", digest(revoked_payload), 2, active.digest,
            revoked_payload, "revoked",
        ),
    )
    assert revoked.status == "revoked" and revoked.active_scope_key is None


def test_tenant_owner_session_and_permission_isolation_are_not_found() -> None:
    repo, _ = repository()
    owner = SemanticPrincipal("tenant-a", "owner-a")
    member(repo, owner)
    payload = compute_contract()
    repo.create(
        owner, contract_id=payload["contract_id"], request_digest=digest(payload),
        idempotency_key="idempotency-create", payload=payload, status="offered",
    )
    for outsider in (SemanticPrincipal("tenant-b", "owner-a"), SemanticPrincipal("tenant-a", "owner-b")):
        with pytest.raises(SemanticContractRepositoryError, match="contract_not_found"):
            repo.get(outsider, payload["contract_id"])
    denied = SemanticPrincipal("tenant-a", "participant")
    repo.put_membership(
        denied, session_id="session-a", epoch=1, permissions={"semantic_compute": False}
    )
    with pytest.raises(SemanticContractRepositoryError, match="session_not_found"):
        repo.require_membership(denied, session_id="session-a", epoch=1, permission="semantic_compute")


def test_legacy_session_without_contract_remains_an_empty_list() -> None:
    repo, _ = repository()
    principal = SemanticPrincipal("tenant-a", "owner-a")
    member(repo, principal, "legacy-session")
    assert repo.list(principal, session_id="legacy-session") == []


def test_migration_upgrade_and_downgrade_preserve_legacy_tables(monkeypatch) -> None:
    db = create_engine("sqlite://")
    with db.begin() as connection:
        connection.execute(sa.text("CREATE TABLE legacy_sessions (id VARCHAR PRIMARY KEY)"))
        migration = importlib.import_module(
            "migrations.versions.c7d8e9f0a1b2_add_semantic_media_contracts"
        )
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        upgraded = set(inspect(connection).get_table_names())
        assert {
            "legacy_sessions", "semantic_session_memberships", "semantic_compute_contracts",
            "semantic_contract_mutations", "semantic_lease_fences", "semantic_compute_leases",
        } <= upgraded
        migration.downgrade()
        assert set(inspect(connection).get_table_names()) == {"legacy_sessions"}
