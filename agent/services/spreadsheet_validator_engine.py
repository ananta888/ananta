"""Deterministic spreadsheet validator engine shared by saga and evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1, canonical_digest


class SpreadsheetValidatorEngine:
    def validate(
        self,
        snapshot: WorkbookSnapshotV1,
        validators: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        sheets = {
            str(sheet["sheet_id"]): {cell["address"]: cell for cell in sheet["cells"]} for sheet in snapshot.sheets
        }
        results: list[dict[str, Any]] = []
        for validator in validators:
            cell = sheets.get(str(validator["sheet_id"]), {}).get(str(validator["cell"]))
            kind = validator["kind"]
            passed = False
            if kind == "equals":
                passed = cell is not None and cell["value"] == validator["expected"]
            elif kind == "number_range":
                value = cell.get("value") if cell else None
                passed = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and validator["minimum"] <= float(value) <= validator["maximum"]
                )
            elif kind == "formula_present":
                passed = cell is not None and cell["formula"] is not None
            elif kind == "cell_empty":
                passed = cell is None or (cell["value"] is None and cell["formula"] is None)
            results.append(
                {
                    "validator_id": validator["validator_id"],
                    "passed": passed,
                    "reason_code": None if passed else "spreadsheet_validator_failed",
                }
            )
        reasons = [item["reason_code"] for item in results if item["reason_code"]]
        return {
            "schema": "ananta.spreadsheet-validation-result.v1",
            "passed": not reasons,
            "results": results,
            "reason_codes": sorted(set(reasons)),
            "validation_digest": canonical_digest(results),
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetValidatorEngine"]
