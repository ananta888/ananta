"""Deterministic bounded reference executor for mock/headless operation."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ananta_contracts.spreadsheet_studio import WorkbookSnapshotV1, canonical_digest, cell_coordinates


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

    def dry_run(
        self,
        *,
        snapshot: Mapping[str, Any],
        actions: tuple[Mapping[str, Any], ...],
        source_artifact: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        del source_artifact
        parsed = WorkbookSnapshotV1.from_mapping(snapshot)
        candidate = copy.deepcopy(parsed.to_dict())
        candidate["snapshot_id"] = f"candidate-{canonical_digest(list(actions))[:24]}"
        cells_by_sheet: dict[str, dict[str, dict[str, Any]]] = {}
        for sheet in candidate["sheets"]:
            cells_by_sheet[sheet["sheet_id"]] = {cell["address"]: cell for cell in sheet["cells"]}
        before_cells = {
            (sheet_id, address): copy.deepcopy(cell)
            for sheet_id, cells in cells_by_sheet.items()
            for address, cell in cells.items()
        }
        direct: dict[tuple[str, str], str] = {}
        for action in actions:
            kind = str(action["kind"])
            action_id = str(action["action_id"])
            if kind in {"set_value", "set_formula", "clear_cell"}:
                sheet_id = str(action["sheet_id"])
                address = str(action["cell"])
                cells = cells_by_sheet[sheet_id]
                before = copy.deepcopy(cells.get(address))
                if kind == "clear_cell":
                    cells.pop(address, None)
                else:
                    cells[address] = {
                        "address": address,
                        "value": action["value"] if kind == "set_value" else None,
                        "formula": copy.deepcopy(action["formula"]),
                        "style_ref": before.get("style_ref") if before else None,
                    }
                direct[(sheet_id, address)] = action_id
            elif kind in {"clear_range", "format_range"}:
                sheet_id = str(action["sheet_id"])
                cells = cells_by_sheet[sheet_id]
                for address in _range_addresses(str(action["start"]), str(action["end"])):
                    if kind == "clear_range":
                        cells.pop(address, None)
                    else:
                        cell = cells.setdefault(
                            address,
                            {"address": address, "value": None, "formula": None, "style_ref": None},
                        )
                        cell["style_ref"] = f"style-{canonical_digest(action['style'])[:24]}"
                    direct[(sheet_id, address)] = action_id
            elif kind == "copy_range":
                source_sheet = str(action["source_sheet_id"])
                target_sheet = str(action["target_sheet_id"])
                source_addresses = _range_addresses(str(action["source_start"]), str(action["source_end"]))
                source_start_row, source_start_column = cell_coordinates(action["source_start"])
                target_start_row, target_start_column = cell_coordinates(action["target_start"])
                copied = [copy.deepcopy(cells_by_sheet[source_sheet].get(address)) for address in source_addresses]
                for source_address, cell in zip(source_addresses, copied, strict=True):
                    source_row, source_column = cell_coordinates(source_address)
                    target_address = _cell_address(
                        target_start_row + source_row - source_start_row,
                        target_start_column + source_column - source_start_column,
                    )
                    if cell is None:
                        cells_by_sheet[target_sheet].pop(target_address, None)
                    else:
                        cell["address"] = target_address
                        cells_by_sheet[target_sheet][target_address] = cell
                    direct[(target_sheet, target_address)] = action_id
            else:
                sheet_id = str(action["sheet_id"])
                cells_by_sheet[sheet_id] = _structural_cells(cells_by_sheet[sheet_id], action)
                for address in cells_by_sheet[sheet_id]:
                    direct[(sheet_id, address)] = action_id
        for sheet in candidate["sheets"]:
            sheet["cells"] = sorted(cells_by_sheet[sheet["sheet_id"]].values(), key=lambda item: item["address"])
        normalized = WorkbookSnapshotV1.from_mapping(candidate)
        after_cells = {
            (sheet_id, address): copy.deepcopy(cell)
            for sheet_id, cells in cells_by_sheet.items()
            for address, cell in cells.items()
        }
        diffs = [
            {
                "action_id": direct.get(key),
                "action_ids": [direct[key]] if key in direct else [],
                "sheet_id": key[0],
                "cell": key[1],
                "before": before_cells.get(key),
                "after": after_cells.get(key),
                "direct": key in direct,
            }
            for key in sorted(set(before_cells) | set(after_cells))
            if before_cells.get(key) != after_cells.get(key)
        ]
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


def _range_addresses(start: str, end: str) -> tuple[str, ...]:
    start_row, start_column = cell_coordinates(start)
    end_row, end_column = cell_coordinates(end)
    return tuple(
        _cell_address(row, column)
        for row in range(start_row, end_row + 1)
        for column in range(start_column, end_column + 1)
    )


def _cell_address(row: int, column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row}"


def _structural_cells(cells: dict[str, dict[str, Any]], action: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    kind = str(action["kind"])
    rows = kind.endswith("rows")
    inserting = kind.startswith("insert")
    start = int(action["start_row"] if rows else action["start_column"])
    count = int(action["count"])
    result = {}
    for address, cell in cells.items():
        row, column = cell_coordinates(address)
        coordinate = row if rows else column
        if not inserting and start <= coordinate < start + count:
            continue
        if coordinate >= start + (0 if inserting else count):
            coordinate += count if inserting else -count
        target_address = _cell_address(coordinate if rows else row, column if rows else coordinate)
        target = copy.deepcopy(cell)
        target["address"] = target_address
        result[target_address] = target
    return result


__all__ = ["DeterministicSpreadsheetMockExecutionAdapter"]
