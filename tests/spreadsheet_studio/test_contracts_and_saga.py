from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import SpreadsheetContractError, WorkbookSnapshotV1
from tests.spreadsheet_studio.helpers import proposal, service, snapshot


def test_schemas_are_closed() -> None:
    for path in Path("schemas/spreadsheet-studio").glob("*.json"):
        schema = json.loads(path.read_text())
        assert schema["additionalProperties"] is False


def test_snapshot_and_formula_contracts_fail_closed() -> None:
    value = snapshot()
    value["sheets"][0]["cells"][0]["address"] = "../../A1"
    with pytest.raises(SpreadsheetContractError, match="cell_invalid"):
        WorkbookSnapshotV1.from_mapping(value)

    value = snapshot()
    value["sheets"][0]["cells"][0]["formula"] = {"op": "external", "url": "https://example.test"}
    with pytest.raises(SpreadsheetContractError, match="formula_op_invalid"):
        WorkbookSnapshotV1.from_mapping(value)


def test_fully_automatic_mock_saga_promotes_exact_validated_candidate(tmp_path: Path) -> None:
    studio = service(tmp_path / "studio.sqlite3")
    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )
    result = studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(document))
    assert result["state"] == "promoted"
    assert result["promoted_version"] == 2
    assert result["validation"]["passed"] is True
    assert result["human_intervention_required"] is False
    current = studio.get_document(tenant_id="tenant-a", document_id="document-one", principal_id="user-a")
    assert current["version"] == 2
    assert current["snapshot"]["sheets"][0]["cells"][0]["value"] == 42

    replay = studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(document))
    assert replay["replayed"] is True


def test_hidden_sheet_stale_version_and_replay_conflict_are_rejected(tmp_path: Path) -> None:
    studio = service(tmp_path / "studio.sqlite3")
    hidden_document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Hidden",
        snapshot=snapshot(hidden=True),
        document_id="hidden-document",
    )
    with pytest.raises(PermissionError, match="hidden_sheet_write_denied"):
        studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=proposal(hidden_document))

    document = studio.create_document(
        tenant_id="tenant-a",
        owner_id="user-a",
        title="Budget",
        snapshot=snapshot(),
        document_id="document-one",
    )
    first = proposal(document)
    studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=first)
    conflict = copy.deepcopy(first)
    conflict["actions"][0]["value"] = 7
    with pytest.raises(SpreadsheetStoreConflict, match="replay_conflict"):
        studio.execute_proposal(tenant_id="tenant-a", principal_id="user-a", proposal=conflict)
