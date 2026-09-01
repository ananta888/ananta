"""Fail-closed admission policy for spreadsheet documents and actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.spreadsheet_studio import SpreadsheetProposalV1, WorkbookSnapshotV1, cell_coordinates


@dataclass(frozen=True, slots=True)
class SpreadsheetPolicy:
    enabled: bool
    mode: str
    automatic_promotion_enabled: bool
    max_actions: int = 1_000
    max_affected_cells: int = 100_000

    def validate(self) -> None:
        if self.mode not in {"disabled", "mock", "worker"}:
            raise ValueError("spreadsheet_policy_mode_invalid")
        if self.enabled != (self.mode != "disabled"):
            raise ValueError("spreadsheet_policy_enabled_mode_mismatch")
        if not 1 <= self.max_actions <= 1_000:
            raise ValueError("spreadsheet_policy_action_limit_invalid")
        if not 1 <= self.max_affected_cells <= 100_000:
            raise ValueError("spreadsheet_policy_cell_limit_invalid")

    def admit(self, snapshot: WorkbookSnapshotV1, proposal: SpreadsheetProposalV1) -> None:
        if not self.enabled:
            raise PermissionError("spreadsheet_studio_disabled")
        if len(proposal.actions) > self.max_actions:
            raise PermissionError("spreadsheet_action_limit_exceeded")
        sheets = {str(sheet["sheet_id"]): sheet for sheet in snapshot.sheets}
        targets: set[tuple[str, int, int]] = set()
        for action in proposal.actions:
            write_sheet_ids = _write_sheet_ids(action)
            read_sheet_ids = _read_sheet_ids(action)
            if any(sheet_id not in sheets for sheet_id in {*write_sheet_ids, *read_sheet_ids}):
                raise PermissionError("spreadsheet_action_sheet_unknown")
            if any(sheets[sheet_id]["hidden"] for sheet_id in write_sheet_ids):
                raise PermissionError("spreadsheet_hidden_sheet_write_denied")
            if any(sheets[sheet_id]["hidden"] for sheet_id in read_sheet_ids):
                raise PermissionError("spreadsheet_hidden_sheet_read_denied")
            footprint = _write_footprint(action)
            if targets.intersection(footprint):
                raise PermissionError("spreadsheet_action_target_duplicate")
            targets.update(footprint)
            if len(targets) > self.max_affected_cells:
                raise PermissionError("spreadsheet_action_cell_limit_exceeded")


def _write_sheet_ids(action: Mapping[str, Any]) -> set[str]:
    if action["kind"] == "copy_range":
        return {str(action["target_sheet_id"])}
    return {str(action["sheet_id"])}


def _read_sheet_ids(action: Mapping[str, Any]) -> set[str]:
    return {str(action["source_sheet_id"])} if action["kind"] == "copy_range" else set()


def _write_footprint(action: Mapping[str, Any]) -> set[tuple[str, int, int]]:
    kind = str(action["kind"])
    if kind in {"set_value", "set_formula", "clear_cell"}:
        row, column = cell_coordinates(action["cell"])
        return {(str(action["sheet_id"]), row, column)}
    if kind in {"clear_range", "format_range"}:
        return _rect(str(action["sheet_id"]), action["start"], action["end"])
    if kind == "copy_range":
        source_start_row, source_start_column = cell_coordinates(action["source_start"])
        source_end_row, source_end_column = cell_coordinates(action["source_end"])
        target_row, target_column = cell_coordinates(action["target_start"])
        return {
            (str(action["target_sheet_id"]), target_row + row, target_column + column)
            for row in range(source_end_row - source_start_row + 1)
            for column in range(source_end_column - source_start_column + 1)
        }
    # Structural operations are ordered transformations. A sentinel prevents
    # ambiguous repeated operations on the same axis/start while preserving
    # deterministic, non-overlapping sequences.
    axis = 0 if kind.endswith("rows") else -1
    start = int(action["start_row"] if axis == 0 else action["start_column"])
    return {(str(action["sheet_id"]), axis, start)}


def _rect(sheet_id: str, start: object, end: object) -> set[tuple[str, int, int]]:
    start_row, start_column = cell_coordinates(start)
    end_row, end_column = cell_coordinates(end)
    return {
        (sheet_id, row, column)
        for row in range(start_row, end_row + 1)
        for column in range(start_column, end_column + 1)
    }


__all__ = ["SpreadsheetPolicy"]
