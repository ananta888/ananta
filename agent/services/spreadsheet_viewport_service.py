"""Bounded, loss-explicit viewport and tile projections for workbook snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ananta_contracts.spreadsheet_studio import canonical_digest, cell_coordinates, require_cell, require_id
from ananta_contracts.spreadsheet_studio_v2 import parse_workbook_snapshot


class SpreadsheetViewportService:
    def project(
        self,
        *,
        snapshot: Mapping[str, Any],
        sheet_id: str,
        start: str,
        end: str,
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        sheet_key = require_id(sheet_id, "sheet_id")
        normalized_start = require_cell(start, "viewport_start")
        normalized_end = require_cell(end, "viewport_end")
        start_row, start_column = cell_coordinates(normalized_start)
        end_row, end_column = cell_coordinates(normalized_end)
        if start_row > end_row or start_column > end_column:
            raise ValueError("spreadsheet_viewport_order_invalid")
        if (end_row - start_row + 1) * (end_column - start_column + 1) > 10_000:
            raise ValueError("spreadsheet_viewport_too_large")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("spreadsheet_viewport_offset_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("spreadsheet_viewport_limit_invalid")
        parsed = parse_workbook_snapshot(snapshot)
        value = parsed.to_dict()
        sheet = next((item for item in value["sheets"] if item["sheet_id"] == sheet_key), None)
        if sheet is None:
            raise KeyError("spreadsheet_sheet_not_found")
        cells = []
        for cell in sheet["cells"]:
            row, column = cell_coordinates(cell["address"])
            if start_row <= row <= end_row and start_column <= column <= end_column:
                cells.append(dict(cell))
        cells.sort(key=lambda item: cell_coordinates(item["address"]))
        page = cells[offset : offset + limit]
        return {
            "schema": "ananta.spreadsheet-workbook-viewport.v1",
            "snapshot_digest": parsed.digest,
            "sheet_id": sheet_key,
            "range": {"start": normalized_start, "end": normalized_end},
            "tile": {
                "row": start_row,
                "column": start_column,
                "rows": end_row - start_row + 1,
                "columns": end_column - start_column + 1,
            },
            "offset": offset,
            "limit": limit,
            "total": len(cells),
            "has_more": offset + len(page) < len(cells),
            "cells": page,
            "projection_digest": canonical_digest(page),
            "backend_cell_count": sum(len(item["cells"]) for item in value["sheets"]),
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetViewportService"]
