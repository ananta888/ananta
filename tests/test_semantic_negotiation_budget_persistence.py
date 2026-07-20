from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import Flask
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticPrincipal,
)
from agent.routes import semantic_media_contracts as routes
from agent.services.semantic_compute_negotiation import (
    NegotiationLimits,
    SemanticComputeNegotiation,
)
from agent.services.semantic_contract_service import (
    HubContractSigner,
    SemanticContractService,
    SemanticContractServiceError,
)
from agent.services.user_session_tokens import issue_user_access_token


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _stack(*, limits: NegotiationLimits):
    clock = FakeClock()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repository = SemanticContractRepository(db_engine=engine, clock=clock)
    service = SemanticContractService(
        repository,
        negotiation=SemanticComputeNegotiation(limits),
        signer=HubContractSigner(b"n" * 32),
        clock=clock,
        feature_enabled=lambda: True,
    )
    principal = SemanticPrincipal("owner-a", "owner-a")
    repository.put_membership(
        principal,
        session_id="session-a",
        epoch=1,
        role="owner",
        permissions={"semantic_compute": True},
        expires_at=2_000.0,
    )
    return clock, repository, service, principal


def _offer(service: SemanticContractService, principal: SemanticPrincipal):
    return service.create_offer(
        principal,
        session_id="session-a",
        room_id=None,
        epoch=1,
        policy_version="policy-v1",
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
        proposal={"profile": "balanced", "delay_ms": 5_000},
        advertisements=(),
        idempotency_key="budget-offer-0001",
    )


def _counter(
    service: SemanticContractService,
    principal: SemanticPrincipal,
    contract_id: str,
    *,
    revision: int,
    key: str,
    proposal: dict | None = None,
):
    return service.mutate(
        principal,
        contract_id=contract_id,
        session_id="session-a",
        epoch=1,
        action="counter",
        expected_revision=revision,
        idempotency_key=key,
        proposal=proposal or {},
        consent_version=1,
        security_confirmed=True,
        fallback_healthy=True,
    )


def test_round_budget_survives_service_recreation_and_replay_does_not_consume_it() -> None:
    limits = NegotiationLimits(max_rounds=2, max_messages=16, max_elapsed_ms=10_000)
    clock, repository, service, principal = _stack(limits=limits)
    offered = _offer(service, principal)
    countered = _counter(
        service,
        principal,
        offered["contract_id"],
        revision=1,
        key="budget-counter-0001",
    )
    assert countered["negotiation_budget"] == {
        "started_at_ms": 1_000_000,
        "round_count": 2,
        "message_count": 2,
    }

    # A different service instance proves the budget is repository state, not
    # request- or process-local state.
    restarted = SemanticContractService(
        repository,
        negotiation=SemanticComputeNegotiation(limits),
        signer=HubContractSigner(b"n" * 32),
        clock=clock,
        feature_enabled=lambda: True,
    )
    replay = _counter(
        restarted,
        principal,
        offered["contract_id"],
        revision=1,
        key="budget-counter-0001",
    )
    assert replay["idempotent_replay"] is True
    assert replay["negotiation_budget"] == countered["negotiation_budget"]

    with pytest.raises(SemanticContractServiceError, match="round_limit_exceeded") as error:
        _counter(
            restarted,
            principal,
            offered["contract_id"],
            revision=2,
            key="budget-counter-0002",
        )
    assert error.value.reason_code == "round_limit_exceeded"
    persisted = repository.get(principal, offered["contract_id"])
    assert (persisted.negotiation_round_count, persisted.negotiation_message_count) == (2, 2)


def test_duration_uses_persisted_start_after_restart_and_offer_replay_still_succeeds() -> None:
    limits = NegotiationLimits(max_rounds=8, max_messages=16, max_elapsed_ms=500)
    clock, repository, service, principal = _stack(limits=limits)
    offered = _offer(service, principal)
    clock.now += 0.501
    restarted = SemanticContractService(
        repository,
        negotiation=SemanticComputeNegotiation(limits),
        signer=HubContractSigner(b"n" * 32),
        clock=clock,
        feature_enabled=lambda: True,
    )

    replay = _offer(restarted, principal)
    assert replay["idempotent_replay"] is True
    assert replay["negotiation_budget"]["started_at_ms"] == 1_000_000
    with pytest.raises(SemanticContractServiceError, match="negotiation_timeout") as error:
        _counter(
            restarted,
            principal,
            offered["contract_id"],
            revision=1,
            key="duration-counter-0001",
        )
    assert error.value.reason_code == "negotiation_timeout"
    assert repository.get(principal, offered["contract_id"]).revision == 1


def test_stale_cas_and_idempotency_conflict_cannot_advance_budget() -> None:
    limits = NegotiationLimits(max_rounds=8, max_messages=16, max_elapsed_ms=10_000)
    _clock, repository, service, principal = _stack(limits=limits)
    offered = _offer(service, principal)
    _counter(
        service,
        principal,
        offered["contract_id"],
        revision=1,
        key="cas-counter-0001",
    )
    with pytest.raises(SemanticContractServiceError, match="stale_revision"):
        _counter(
            service,
            principal,
            offered["contract_id"],
            revision=1,
            key="cas-counter-0002",
        )
    with pytest.raises(SemanticContractServiceError, match="idempotency_conflict"):
        _counter(
            service,
            principal,
            offered["contract_id"],
            revision=1,
            key="cas-counter-0001",
            proposal={"profile": "conservative"},
        )
    persisted = repository.get(principal, offered["contract_id"])
    assert persisted.revision == 2
    assert (persisted.negotiation_round_count, persisted.negotiation_message_count) == (2, 2)


def test_message_budget_is_enforced_across_http_requests_and_replays(monkeypatch) -> None:
    limits = NegotiationLimits(max_rounds=8, max_messages=2, max_elapsed_ms=10_000)
    _clock, _repository, service, _principal = _stack(limits=limits)
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SEMANTIC_COMPUTE_SECURITY_CONFIRMED=True,
        SEMANTIC_COMPUTE_FALLBACK_HEALTHY=True,
    )
    app.register_blueprint(routes.semantic_media_contracts_bp)
    monkeypatch.setattr(routes, "get_semantic_contract_service", lambda: service)
    monkeypatch.setattr(routes, "_establish_membership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_require_semantic_capability", lambda *_args, **_kwargs: None)
    client = app.test_client()
    token = issue_user_access_token(username="owner-a", role="admin")
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"

    body = {
        "session_id": "session-a",
        "epoch": 1,
        "policy_version": "policy-v1",
        "consent_version": 1,
        "proposal": {"profile": "balanced", "delay_ms": 5_000},
    }
    offered = client.post(
        "/v1/semantic-media/contracts",
        json=body,
        headers={"Idempotency-Key": "http-offer-0001"},
    )
    assert offered.status_code == 201
    contract_id = offered.json["contract"]["contract_id"]
    mutation_body = {"session_id": "session-a", "epoch": 1, "consent_version": 1}
    countered = client.post(
        f"/v1/semantic-media/contracts/{contract_id}/counter",
        json=mutation_body,
        headers={"Idempotency-Key": "http-counter-0001", "If-Match": '"1"'},
    )
    assert countered.status_code == 200
    assert countered.json["contract"]["negotiation_budget"]["message_count"] == 2
    replay = client.post(
        f"/v1/semantic-media/contracts/{contract_id}/counter",
        json=mutation_body,
        headers={"Idempotency-Key": "http-counter-0001", "If-Match": '"1"'},
    )
    assert replay.status_code == 200
    assert replay.json["contract"]["idempotent_replay"] is True
    exhausted = client.post(
        f"/v1/semantic-media/contracts/{contract_id}/counter",
        json=mutation_body,
        headers={"Idempotency-Key": "http-counter-0002", "If-Match": '"2"'},
    )
    assert exhausted.status_code == 409
    assert exhausted.json["error"]["code"] == "message_limit_exceeded"


def test_migration_backfills_legacy_contracts_and_receipts_conservatively(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE semantic_compute_contracts "
                "(id VARCHAR PRIMARY KEY, revision INTEGER NOT NULL, created_at FLOAT NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE semantic_contract_mutations "
                "(id VARCHAR PRIMARY KEY, result_revision INTEGER NOT NULL, created_at FLOAT NOT NULL)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO semantic_compute_contracts (id, revision, created_at) VALUES ('contract-a', 3, 1234.5)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO semantic_contract_mutations (id, result_revision, created_at) "
                "VALUES ('receipt-a', 2, 1234.0)"
            )
        )
        migration = importlib.import_module("migrations.versions.ff4a5b6c7d8e_add_semantic_negotiation_budgets")
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        contract = connection.execute(
            sa.text(
                "SELECT negotiation_started_at_ms, negotiation_round_count, "
                "negotiation_message_count FROM semantic_compute_contracts"
            )
        ).one()
        receipt = connection.execute(
            sa.text(
                "SELECT result_negotiation_started_at_ms, result_negotiation_round_count, "
                "result_negotiation_message_count FROM semantic_contract_mutations"
            )
        ).one()
        assert tuple(contract) == (1_234_500, 3, 3)
        assert tuple(receipt) == (1_234_000, 2, 2)

        migration.downgrade()
        assert "negotiation_started_at_ms" not in {
            column["name"] for column in inspect(connection).get_columns("semantic_compute_contracts")
        }
