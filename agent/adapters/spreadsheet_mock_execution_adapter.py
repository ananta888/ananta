"""Deterministic bounded reference executor for mock/headless operation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1, canonical_digest


class DeterministicSpreadsheetMockExecutionAdapter:
    @property
    def capability(self) -> Mapping[str, Any]:
        return {
            "schema": "ananta.spreadsheet-executor-capability.v1",
            "state": "available",
            "engine": "deterministic-mock",
            "engine_version": "v1",
            "network_enabled": False,
            "macros_enabled": False,
            "external_links_enabled": False,
            "production_fidelity": False,
        }

    def dry_run(self, *, snapshot: Mapping[str, Any], actions: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any]:
        parsed = WorkbookSnapshotV1.from_mapping(snapshot)
        candidate = copy.deepcopy(parsed.to_dict())
        candidate["snapshot_id"] = f"candidate-{canonical_digest(list(actions))[:24]}"
        cells_by_sheet: dict[str, dict[str, dict[str, Any]]] = {}
        for sheet in candidate["sheets"]:
            cells_by_sheet[sheet["sheet_id"]] = {cell["address"]: cell for cell in sheet["cells"]}
        diffs: list[dict[str, Any]] = []
        for action in actions:
            sheet_id = str(action["sheet_id"])
            address = str(action["cell"])
            cells = cells_by_sheet[sheet_id]
            before = copy.deepcopy(cells.get(address))
            if action["kind"] == "clear_cell":
                cells.pop(address, None)
            else:
                cells[address] = {
                    "address": address,
                    "value": action["value"] if action["kind"] == "set_value" else None,
                    "formula": copy.deepcopy(action["formula"]),
                    "style_ref": before.get("style_ref") if before else None,
                }
            after = copy.deepcopy(cells.get(address))
            diffs.append(
                {
                    "action_id": action["action_id"],
                    "sheet_id": sheet_id,
                    "cell": address,
                    "before": before,
                    "after": after,
                    "direct": True,
                }
            )
        for sheet in candidate["sheets"]:
            sheet["cells"] = sorted(cells_by_sheet[sheet["sheet_id"]].values(), key=lambda item: item["address"])
        normalized = WorkbookSnapshotV1.from_mapping(candidate)
        return {
            "schema": "ananta.spreadsheet-execution-result.v1",
            "candidate_snapshot": normalized.to_dict(),
            "candidate_snapshot_digest": normalized.digest,
            "diff": diffs,
            "recalculation_performed": False,
            "engine": "deterministic-mock",
            "production_fidelity": False,
            "human_intervention_required": False,
        }


__all__ = ["DeterministicSpreadsheetMockExecutionAdapter"]
