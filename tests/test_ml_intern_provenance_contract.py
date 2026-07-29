from __future__ import annotations

import pytest

import agent.services.ml_intern_training_contract as training_contract
from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)
from agent.services.unsloth_evidence import (
    EvidenceVerificationError,
    ProvidedEvidenceRegistry,
)
from scripts.run_lora_training_smoke import _normalize_evidence_ids


SOURCE_ID = "SRC_supplied-model"
RUN_ID = "RUN_supplied-evaluation"


def test_training_contract_reexports_the_canonical_provenance_contract() -> None:
    assert training_contract.normalize_source_ids is normalize_source_ids
    assert training_contract.normalize_run_ids is normalize_run_ids
    assert training_contract.MlInternTrainingContractError is MlInternTrainingContractError


def test_canonical_provenance_normalizes_sorts_and_rejects_duplicates() -> None:
    assert normalize_source_ids([" SRC_repo:7 ", SOURCE_ID]) == (
        "SRC_repo:7",
        SOURCE_ID,
    )
    assert normalize_run_ids([RUN_ID, "RUN_training:9"]) == (
        RUN_ID,
        "RUN_training:9",
    )

    with pytest.raises(MlInternTrainingContractError) as duplicate:
        normalize_source_ids([SOURCE_ID, SOURCE_ID])
    assert duplicate.value.reason_code == "source_ids_duplicate"


def test_canonical_provenance_preserves_productive_length_and_bound_codes() -> None:
    assert normalize_source_ids(["SRC_" + ("a" * 188)])
    with pytest.raises(MlInternTrainingContractError) as too_long:
        normalize_source_ids(["SRC_" + ("a" * 189)])
    assert too_long.value.reason_code == "source_ids_invalid"

    with pytest.raises(MlInternTrainingContractError) as too_many:
        normalize_run_ids([RUN_ID] * 129)
    assert too_many.value.reason_code == "run_ids_invalid"


def test_evidence_registry_adds_membership_without_silent_deduplication() -> None:
    registry = ProvidedEvidenceRegistry(
        source_ids=[SOURCE_ID],
        run_ids=[RUN_ID],
    )
    assert registry.resolve(source_ids=[SOURCE_ID], run_ids=[RUN_ID]).source_ids == (
        SOURCE_ID,
    )

    with pytest.raises(EvidenceVerificationError) as unknown:
        registry.require_source("SRC_invented")
    assert unknown.value.code == "source_id_unknown"

    with pytest.raises(EvidenceVerificationError) as duplicate:
        registry.resolve(
            source_ids=[SOURCE_ID, SOURCE_ID],
            run_ids=[RUN_ID],
        )
    assert duplicate.value.code == "source_ids_duplicate"


def test_smoke_uses_canonical_sorting_and_duplicate_rejection_after_csv_expansion() -> None:
    normalized = _normalize_evidence_ids(
        src_ids=(f"{SOURCE_ID},SRC_repo:7",),
        run_ids=("RUN_training:9", RUN_ID),
    )
    assert normalized["src_ids"] == ["SRC_repo:7", SOURCE_ID]
    assert normalized["run_ids"] == [RUN_ID, "RUN_training:9"]

    with pytest.raises(MlInternTrainingContractError) as duplicate:
        _normalize_evidence_ids(
            src_ids=(SOURCE_ID, SOURCE_ID),
            run_ids=(RUN_ID,),
        )
    assert duplicate.value.reason_code == "source_ids_duplicate"
