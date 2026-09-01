"""Deterministic paginated actual diff across V1 and rich V2 snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.spreadsheet_studio import canonical_digest
from ananta_contracts.spreadsheet_studio_v2 import execution_snapshot, parse_workbook_snapshot


class SpreadsheetActualDiffService:
    def build(
        self,
        *,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        execution_diff: Sequence[Mapping[str, Any]] = (),
        actions: Sequence[Mapping[str, Any]] = (),
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        items = self.complete_items(
            before=before,
            after=after,
            execution_diff=execution_diff,
            actions=actions,
        )
        return self.paginate(items, offset=offset, limit=limit)

    def paginate(
        self,
        items: Sequence[Mapping[str, Any]],
        *,
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("spreadsheet_diff_offset_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("spreadsheet_diff_limit_invalid")
        complete = [dict(item) for item in items]
        page = complete[offset : offset + limit]
        return {
            "schema": "ananta.spreadsheet-actual-diff.v1",
            "offset": offset,
            "limit": limit,
            "total": len(complete),
            "has_more": offset + len(page) < len(complete),
            "items": page,
            "diff_digest": canonical_digest(complete),
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }

    def complete_items(
        self,
        *,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        execution_diff: Sequence[Mapping[str, Any]] = (),
        actions: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Return the bounded complete set for internal Hub validation."""

        direct = {
            (str(item.get("sheet_id") or ""), str(item.get("cell") or "")): {
                "direct": bool(item.get("direct")),
                "action_ids": sorted({str(value) for value in list(item.get("action_ids") or []) if str(value)}),
            }
            for item in execution_diff
        }
        items = self._cell_items(before, after, direct=direct)
        items.extend(self._structure_items(actions))
        items.extend(self._rich_items(before, after))
        items.sort(key=self._sort_key)
        return items

    @staticmethod
    def _structure_items(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "kind": "structure",
                "object_id": str(action["action_id"]),
                "sheet_id": str(action["sheet_id"]),
                "cell": None,
                "change_kinds": [str(action["kind"])],
                "before": None,
                "after": dict(action),
                "direct": True,
                "action_ids": [str(action["action_id"])],
            }
            for action in actions
            if str(action.get("kind") or "") in {"insert_rows", "delete_rows", "insert_columns", "delete_columns"}
        ]

    @staticmethod
    def _cell_items(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        direct: Mapping[tuple[str, str], Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        before_cells = _cells(before)
        after_cells = _cells(after)
        items = []
        for key in sorted(set(before_cells) | set(after_cells)):
            previous, current = before_cells.get(key), after_cells.get(key)
            if previous == current:
                continue
            binding = dict(direct.get(key) or {})
            items.append(
                {
                    "kind": "cell",
                    "object_id": f"{key[0]}:{key[1]}",
                    "sheet_id": key[0],
                    "cell": key[1],
                    "change_kinds": _cell_change_kinds(previous, current),
                    "before": previous,
                    "after": current,
                    "direct": bool(binding.get("direct")),
                    "action_ids": list(binding.get("action_ids") or []),
                }
            )
        return items

    @staticmethod
    def _rich_items(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
        parsed_before = parse_workbook_snapshot(before)
        parsed_after = parse_workbook_snapshot(after)
        if not hasattr(parsed_before, "value") and not hasattr(parsed_after, "value"):
            return []
        before_value = parsed_before.to_dict()
        after_value = parsed_after.to_dict()
        items = []
        before_sheets = {sheet["sheet_id"]: sheet for sheet in before_value["sheets"]}
        after_sheets = {sheet["sheet_id"]: sheet for sheet in after_value["sheets"]}
        visibility_field = "visibility" if before_value.get("schema", "").endswith(".v2") else "hidden"
        for sheet_id in sorted(set(before_sheets) & set(after_sheets)):
            old = before_sheets[sheet_id].get(visibility_field)
            new = after_sheets[sheet_id].get(visibility_field)
            if old != new:
                items.append(_object_item("sheet_visibility", sheet_id, old, new, sheet_id=sheet_id))
        for collection, id_field, kind in (
            ("styles", "style_id", "style"),
            ("named_ranges", "named_range_id", "named_range"),
            ("tables", "table_id", "table"),
            ("charts", "chart_id", "chart"),
            ("unsupported_objects", "object_id", "unsupported_object"),
        ):
            old_items = {str(item[id_field]): item for item in before_value.get(collection, [])}
            new_items = {str(item[id_field]): item for item in after_value.get(collection, [])}
            for object_id in sorted(set(old_items) | set(new_items)):
                if old_items.get(object_id) != new_items.get(object_id):
                    sheet_id = str((new_items.get(object_id) or old_items.get(object_id) or {}).get("sheet_id") or "")
                    items.append(
                        _object_item(
                            kind,
                            object_id,
                            old_items.get(object_id),
                            new_items.get(object_id),
                            sheet_id=sheet_id or None,
                        )
                    )
        old_dependencies = {canonical_digest(item): item for item in before_value.get("dependencies", [])}
        new_dependencies = {canonical_digest(item): item for item in after_value.get("dependencies", [])}
        for object_id in sorted(set(old_dependencies) | set(new_dependencies)):
            if old_dependencies.get(object_id) != new_dependencies.get(object_id):
                item = new_dependencies.get(object_id) or old_dependencies.get(object_id) or {}
                items.append(
                    _object_item(
                        "dependency",
                        object_id,
                        old_dependencies.get(object_id),
                        new_dependencies.get(object_id),
                        sheet_id=str(item.get("to_sheet_id") or "") or None,
                    )
                )
        return items

    @staticmethod
    def _sort_key(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(item.get("kind") or ""),
            str(item.get("sheet_id") or ""),
            str(item.get("cell") or ""),
            str(item.get("object_id") or ""),
        )


def _cells(snapshot: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    parsed = parse_workbook_snapshot(snapshot)
    if hasattr(parsed, "value"):
        return {
            (str(sheet["sheet_id"]), str(cell["address"])): dict(cell)
            for sheet in parsed.to_dict()["sheets"]
            for cell in sheet["cells"]
        }
    v1 = execution_snapshot(parsed)
    return {
        (str(sheet["sheet_id"]), str(cell["address"])): dict(cell) for sheet in v1.sheets for cell in sheet["cells"]
    }


def _cell_change_kinds(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[str]:
    if before is None:
        return ["created"]
    if after is None:
        return ["deleted"]
    fields = (
        ("value", ("value", "raw_value", "displayed_value")),
        ("formula", ("formula", "formula_text", "formula_ast")),
        ("style", ("style_ref", "style_id")),
        ("coordinate", ("row", "column", "address")),
    )
    changes = []
    for kind, names in fields:
        if any(before.get(name) != after.get(name) for name in names if name in before or name in after):
            changes.append(kind)
    return changes or ["metadata"]


def _object_item(
    kind: str,
    object_id: str,
    before: Any,
    after: Any,
    *,
    sheet_id: str | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "object_id": object_id,
        "sheet_id": sheet_id,
        "cell": None,
        "change_kinds": ["created" if before is None else "deleted" if after is None else "updated"],
        "before": before,
        "after": after,
        "direct": False,
        "action_ids": [],
    }


__all__ = ["SpreadsheetActualDiffService"]
