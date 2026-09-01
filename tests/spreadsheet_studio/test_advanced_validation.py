from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from jsonschema import Draft202012Validator
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, create_engine

import agent.db_models  # noqa: F401 - registers SQLModel metadata
from agent.adapters.spreadsheet_mock_execution_adapter import DeterministicSpreadsheetMockExecutionAdapter
from agent.repositories.spreadsheet_validation_reference_repository import (
    SqlSpreadsheetValidationReferenceRepository,
)
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStore, SpreadsheetStoreConflict
from agent.services.spreadsheet_validation_reference_store import SpreadsheetValidationReferenceStore
from agent.services.spreadsheet_validator_engine import SpreadsheetValidatorEngine
from ananta_contracts.spreadsheet_studio import SpreadsheetContractError, canonical_digest, validate_validator
from tests.spreadsheet_studio.helpers import snapshot
from tests.spreadsheet_studio.test_rich_workbook_semantics import rich_snapshot


def _validators() -> list[dict]:
    return [
        {
            "validator_id": "tolerance",
            "kind": "number_tolerance",
            "sheet_id": "sheet-one",
            "cell": "A1",
            "expected": 0.3,
            "absolute_tolerance": 0.000001,
            "relative_tolerance": 0,
            "rounding_digits": 6,
        },
        {
            "validator_id": "formula",
            "kind": "formula_pattern",
            "sheet_id": "sheet-one",
            "cell": "B2",
            "expected_formula": {"op": "cell", "sheet_id": "sheet-one", "cell": "A1"},
            "expected_origin": "B1",
            "allow_relative_references": True,
        },
        {
            "validator_id": "positive",
            "kind": "invariant",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A2",
            "rule": "non_negative",
        },
        {
            "validator_id": "sum",
            "kind": "sum_range",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A2",
            "expected": 2.3,
            "absolute_tolerance": 0.000001,
            "relative_tolerance": 0,
            "rounding_digits": 6,
        },
        {
            "validator_id": "range",
            "kind": "range_rule",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A2",
            "value_type": "number",
            "allow_empty": False,
            "minimum": 0,
            "maximum": 10,
        },
        {
            "validator_id": "change",
            "kind": "change_scope",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A2",
            "expectation": "unchanged",
        },
    ]


def _validation_snapshot() -> dict:
    value = snapshot()
    value["sheets"][0]["cells"] = [
        {"address": "A1", "value": 0.30000000000000004, "formula": None, "style_ref": None},
        {"address": "A2", "value": 2, "formula": None, "style_ref": None},
        {
            "address": "B2",
            "value": 2,
            "formula": {"op": "cell", "sheet_id": "sheet-one", "cell": "A2"},
            "style_ref": None,
        },
    ]
    return value


def _reference_validator(reference_id: str = "golden-one") -> dict:
    return {
        "validator_id": "reference",
        "kind": "reference_range",
        "reference_id": reference_id,
        "reference_sheet_id": "sheet-one",
        "reference_start": "A1",
        "reference_end": "B1",
        "sheet_id": "sheet-one",
        "start": "A1",
        "end": "B1",
        "absolute_tolerance": 0,
        "relative_tolerance": 0,
        "compare_formulas": True,
    }


def _studio(tmp_path):
    references = SpreadsheetValidationReferenceStore(tmp_path / "references.sqlite3")
    studio = SpreadsheetSagaService(
        SpreadsheetStore(tmp_path / "documents.sqlite3"),
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=True),
        executor=DeterministicSpreadsheetMockExecutionAdapter(),
        validator_engine=SpreadsheetValidatorEngine(references),
        validation_references=references,
    )
    return studio, references


def test_additive_validator_union_is_closed_bounded_and_negative() -> None:
    normalized = [validate_validator(value) for value in _validators()]
    validator_schema = json.loads(
        (Path("schemas/spreadsheet-studio") / "validator.v2.json").read_text(encoding="utf-8")
    )
    for value in normalized:
        Draft202012Validator(validator_schema).validate(value)
    assert {value["kind"] for value in normalized} == {
        "number_tolerance",
        "formula_pattern",
        "invariant",
        "sum_range",
        "range_rule",
        "change_scope",
    }
    reference_validator = validate_validator(_reference_validator())
    assert reference_validator["kind"] == "reference_range"
    Draft202012Validator(validator_schema).validate(reference_validator)
    malformed = _validators()[0]
    malformed["network_url"] = "https://example.test"
    with pytest.raises(SpreadsheetContractError, match="fields_invalid"):
        validate_validator(malformed)
    malformed = _reference_validator()
    malformed["end"] = "C1"
    with pytest.raises(SpreadsheetContractError, match="reference_range_invalid"):
        validate_validator(malformed)


def test_engine_reports_tolerance_relative_formula_invariants_and_unexpected_changes() -> None:
    validators = [validate_validator(value) for value in _validators()]
    result = SpreadsheetValidatorEngine().validate(
        _validation_snapshot(),
        validators,
        actual_diff={
            "total": 1,
            "items": [{"kind": "cell", "object_id": "sheet-one:A1", "sheet_id": "sheet-one", "cell": "A1"}],
        },
    )
    assert [item["passed"] for item in result["results"]] == [True, True, True, True, True, False]
    assert result["correctness"] == "partially_correct"
    assert result["change_classification"] == "unexpected"
    assert result["outcome"] == "unexpectedly_changed"
    assert all(len(value) == 64 for value in result["bindings"].values())
    schema = json.loads((Path("schemas/spreadsheet-studio") / "validation-result.v2.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(result)
    validation_digest = result.pop("validation_digest")
    assert canonical_digest(result) == validation_digest


def test_reference_artifact_is_immutable_tenant_bound_and_drives_automatic_validation(tmp_path) -> None:
    studio, references = _studio(tmp_path)
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="owner-a",
        title="Golden",
        snapshot=snapshot(),
        document_id="golden-document",
    )
    reference = studio.create_validation_reference(
        tenant_id="tenant-a",
        principal_id="owner-a",
        reference_id="golden-one",
        document_id="golden-document",
        version=1,
    )
    assert reference["snapshot_digest"] == document["snapshot_digest"]
    reference_payload = dict(reference)
    reference_digest = reference_payload.pop("reference_digest")
    assert canonical_digest(reference_payload) == reference_digest
    with pytest.raises(SpreadsheetStoreConflict, match="reference_exists"):
        studio.create_validation_reference(
            tenant_id="tenant-a",
            principal_id="owner-a",
            reference_id="golden-one",
            document_id="golden-document",
            version=1,
        )
    with pytest.raises(KeyError, match="reference_not_found"):
        references.get_reference("tenant-b", "golden-one")
    with pytest.raises(PermissionError, match="owner_required"):
        studio.get_validation_reference(
            tenant_id="tenant-a",
            principal_id="owner-b",
            reference_id="golden-one",
        )

    proposal = {
        "schema": "ananta.spreadsheet-proposal.v1",
        "proposal_id": "reference-proposal",
        "document_id": document["document_id"],
        "expected_version": 1,
        "base_snapshot_digest": document["snapshot_digest"],
        "actions": [
            {
                "action_id": "same-value",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 1,
                "formula": None,
            }
        ],
        "validators": [_reference_validator()],
        "automatic_promotion": True,
    }
    result = studio.execute_proposal(tenant_id="tenant-a", principal_id="owner-a", proposal=proposal)
    assert result["state"] == "promoted"
    assert result["validation"]["outcome"] == "unchanged"
    assert result["validation"]["results"][0]["reference_digest"] == reference["reference_digest"]
    assert result["validation"]["bindings"]["candidate_digest"] == result["candidate_snapshot_digest"]


def test_missing_reference_is_automatically_not_verifiable() -> None:
    validator = validate_validator(_reference_validator("missing-reference"))
    result = SpreadsheetValidatorEngine().validate(_validation_snapshot(), [validator], tenant_id="tenant-a")
    assert result["passed"] is False
    assert result["outcome"] == "not_verifiable"
    assert result["human_intervention_required"] is False
    incomplete_diff = SpreadsheetValidatorEngine().validate(
        _validation_snapshot(),
        [validate_validator(_validators()[-1])],
        actual_diff={"total": 2, "items": []},
    )
    assert incomplete_diff["outcome"] == "not_verifiable"
    assert incomplete_diff["reason_codes"] == ["spreadsheet_validation_diff_incomplete"]


def test_validation_outcomes_cover_correct_unsafe_errors_locale_dates_and_indirect_changes() -> None:
    engine = SpreadsheetValidatorEngine()
    correct = engine.validate(
        _validation_snapshot(),
        [validate_validator(value) for value in _validators()[:-1]],
        actual_diff={"total": 1, "items": []},
    )
    assert correct["passed"] is True
    assert correct["outcome"] == correct["correctness"] == "correct"
    assert correct["technically_valid"] is True

    error_snapshot = _validation_snapshot()
    error_snapshot["sheets"][0]["cells"][0]["value"] = "#DIV/0!"
    no_errors = validate_validator(
        {
            "validator_id": "errors",
            "kind": "invariant",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A2",
            "rule": "no_errors",
        }
    )
    assert engine.validate(error_snapshot, [no_errors])["correctness"] == "incorrect"

    indirect = validate_validator(
        {
            "validator_id": "indirect",
            "kind": "change_scope",
            "sheet_id": "sheet-one",
            "start": "A1",
            "end": "A1",
            "expectation": "changed",
        }
    )
    indirect_result = engine.validate(
        _validation_snapshot(),
        [indirect],
        actual_diff={
            "total": 1,
            "items": [
                {
                    "kind": "cell",
                    "object_id": "sheet-one:A1",
                    "sheet_id": "sheet-one",
                    "cell": "A1",
                    "direct": False,
                }
            ],
        },
    )
    assert indirect_result["passed"] is True

    de_1900 = rich_snapshot()
    en_1904 = copy.deepcopy(de_1900)
    en_1904["locale"] = "en-US"
    en_1904["timezone"] = "America/New_York"
    en_1904["date_system"] = "1904"
    first = engine.validate(de_1900, [])
    second = engine.validate(en_1904, [])
    assert first["bindings"]["recalc_digest"] != second["bindings"]["recalc_digest"]

    unsafe_snapshot = rich_snapshot()
    unsafe_snapshot["unsupported_objects"] = [
        {"object_id": "macro-one", "kind": "macro", "reason_code": "spreadsheet_macro_forbidden"}
    ]
    unsafe = engine.validate(unsafe_snapshot, [], actual_diff={"total": 1, "items": []})
    assert unsafe["passed"] is False
    assert unsafe["outcome"] == unsafe["safety"] == "unsafe"


def test_sql_reference_repository_detects_tampering_and_migration_is_reversible(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'references.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    repository = SqlSpreadsheetValidationReferenceRepository(db_engine=engine)
    value = {
        "schema": "ananta.spreadsheet-validation-reference.v1",
        "reference_id": "reference-one",
        "document_id": "document-one",
        "document_version": 1,
        "owner_id": "owner-one",
        "tenant_digest": canonical_digest({"tenant_id": "tenant-a"}),
        "snapshot_schema": snapshot()["schema"],
        "snapshot_digest": canonical_digest(snapshot()),
        "snapshot": snapshot(),
        "source_grounding_verified": False,
        "human_intervention_required": False,
    }
    value["reference_digest"] = canonical_digest(value)
    # The reference FK deliberately requires its Hub-owned document.
    from agent.repositories.spreadsheet_document_repository import SqlSpreadsheetDocumentRepository

    SqlSpreadsheetDocumentRepository(db_engine=engine).create_document(
        "tenant-a",
        {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": "document-one",
            "owner_id": "owner-one",
            "snapshot_digest": value["snapshot_digest"],
            "snapshot": value["snapshot"],
            "state": "published",
        },
    )
    repository.create_reference("tenant-a", value)
    assert repository.get_reference("tenant-a", "reference-one")["reference_digest"] == value["reference_digest"]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE spreadsheet_validation_references SET payload_json='{}' "
                "WHERE tenant_id='tenant-a' AND reference_id='reference-one'"
            )
        )
    with pytest.raises(RuntimeError, match="reference_integrity_failed"):
        repository.get_reference("tenant-a", "reference-one")

    documents = importlib.import_module("migrations.versions.b9d1f3a5c7e0_add_spreadsheet_document_persistence")
    references_migration = importlib.import_module(
        "migrations.versions.e2a4c6d8f0b3_add_spreadsheet_validation_references"
    )
    migration_engine = create_engine("sqlite://")
    with migration_engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(documents, "op", operations)
        monkeypatch.setattr(references_migration, "op", operations)
        documents.upgrade()
        references_migration.upgrade()
        assert "spreadsheet_validation_references" in inspect(connection).get_table_names()
        references_migration.downgrade()
        assert "spreadsheet_validation_references" not in inspect(connection).get_table_names()
        documents.downgrade()
