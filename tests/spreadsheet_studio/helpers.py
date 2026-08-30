from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.adapters.spreadsheet_mock_execution_adapter import (
    DeterministicSpreadsheetMockExecutionAdapter,
)
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_store import SpreadsheetStore
from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1


def snapshot(*, hidden: bool = False) -> dict[str, Any]:
    return {
        "schema": "ananta.spreadsheet-workbook-snapshot.v1",
        "snapshot_id": "snapshot-one",
        "document_version_id": "document-version-one",
        "sheets": [
            {
                "sheet_id": "sheet-one",
                "name": "Sheet 1",
                "hidden": hidden,
                "cells": [
                    {"address": "A1", "value": 1, "formula": None, "style_ref": None},
                    {"address": "B1", "value": "safe", "formula": None, "style_ref": None},
                ],
            }
        ],
    }


def proposal(document: dict[str, Any], *, proposal_id: str = "proposal-one") -> dict[str, Any]:
    return {
        "schema": "ananta.spreadsheet-proposal.v1",
        "proposal_id": proposal_id,
        "document_id": document["document_id"],
        "expected_version": document["version"],
        "base_snapshot_digest": document["snapshot_digest"],
        "actions": [
            {
                "action_id": "action-one",
                "kind": "set_value",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "value": 42,
                "formula": None,
            }
        ],
        "validators": [
            {
                "validator_id": "validator-one",
                "kind": "equals",
                "sheet_id": "sheet-one",
                "cell": "A1",
                "expected": 42,
                "minimum": None,
                "maximum": None,
            }
        ],
        "automatic_promotion": True,
    }


def service(path: Path, *, automatic_promotion: bool = True) -> SpreadsheetSagaService:
    return SpreadsheetSagaService(
        SpreadsheetStore(path),
        policy=SpreadsheetPolicy(enabled=True, mode="mock", automatic_promotion_enabled=automatic_promotion),
        executor=DeterministicSpreadsheetMockExecutionAdapter(),
    )


def parsed_snapshot(*, hidden: bool = False) -> WorkbookSnapshotV1:
    return WorkbookSnapshotV1.from_mapping(snapshot(hidden=hidden))
