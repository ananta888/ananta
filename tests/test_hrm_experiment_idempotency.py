from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

from agent.repositories.hrm_experiments import (
    HrmExperimentRepository,
    HrmRepositoryConflict,
)


def test_mutation_receipt_replays_only_the_same_payload() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repository = HrmExperimentRepository(db_engine=engine, clock=lambda: 10.0)

    receipt, replayed = repository.claim_idempotency(
        tenant_id="tenant-1",
        owner_subject="subject-1",
        operation="register_dataset",
        key_digest="a" * 64,
        request_digest="b" * 64,
    )
    assert replayed is False
    repository.complete_idempotency(
        receipt.id,
        request_digest="b" * 64,
        resource_id="dataset-1",
        response={"dataset_id": "dataset-1"},
    )

    repeated, replayed = repository.claim_idempotency(
        tenant_id="tenant-1",
        owner_subject="subject-1",
        operation="register_dataset",
        key_digest="a" * 64,
        request_digest="b" * 64,
    )
    assert replayed is True
    assert repeated.response == {"dataset_id": "dataset-1"}

    with pytest.raises(HrmRepositoryConflict) as error:
        repository.claim_idempotency(
            tenant_id="tenant-1",
            owner_subject="subject-1",
            operation="register_dataset",
            key_digest="a" * 64,
            request_digest="c" * 64,
        )
    assert error.value.reason_code == "hrm.idempotency_payload_conflict"
