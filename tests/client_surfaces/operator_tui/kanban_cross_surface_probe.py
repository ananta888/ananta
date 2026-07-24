"""Subprocess probe that exercises the real operator-TUI Kanban adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from client_surfaces.operator_tui.dashboard_http_adapter import (
    DashboardHubAdapter,
)
from client_surfaces.operator_tui.dashboard_surfaces import (
    DashboardFeatureFlags,
    DashboardSurfaceController,
    RevisionConflict,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "move"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--target-status")
    parser.add_argument("--idempotency-key", default="cross-surface-probe")
    return parser


def _adapter(arguments: argparse.Namespace) -> DashboardHubAdapter:
    return DashboardHubAdapter(
        endpoint=arguments.endpoint,
        token=arguments.token,
        local_flags=DashboardFeatureFlags(kanban=True, models=False),
        idempotency_key_factory=lambda: arguments.idempotency_key,
    )


def _find_card(
    columns: Sequence[object],
    task_id: str,
) -> dict[str, Any]:
    for raw_column in columns:
        if not isinstance(raw_column, Mapping):
            continue
        column_id = str(raw_column.get("id") or "")
        tasks = raw_column.get("tasks")
        if not isinstance(tasks, Sequence):
            continue
        for raw_task in tasks:
            if not isinstance(raw_task, Mapping):
                continue
            if str(raw_task.get("id") or "") == task_id:
                return {**dict(raw_task), "column_id": column_id}
    raise RuntimeError("cross_surface_task_not_found")


async def _snapshot(arguments: argparse.Namespace) -> dict[str, Any]:
    adapter = _adapter(arguments)
    controller = DashboardSurfaceController(
        kanban_port=adapter,
        model_catalog_port=adapter,
        flags=DashboardFeatureFlags(kanban=True, models=False),
    )
    loaded = await controller.load_kanban()
    columns = loaded.payload.get("columns")
    if not isinstance(columns, list):
        raise RuntimeError("cross_surface_columns_missing")
    return {
        "ok": True,
        "board_revision": loaded.payload.get("revision"),
        "columns": columns,
        "card": _find_card(columns, arguments.task_id),
    }


async def _move(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.expected_revision is None or not arguments.target_status:
        raise ValueError("move requires expected revision and target status")
    adapter = _adapter(arguments)
    try:
        card = await adapter.move_task(
            arguments.task_id,
            target_status=arguments.target_status,
            target_position=0,
            expected_revision=arguments.expected_revision,
        )
    except RevisionConflict as exc:
        return {
            "ok": False,
            "error": {
                "http_status": 409,
                "code": str(exc),
                "current_revision": exc.current_revision,
            },
        }
    return {"ok": True, "card": dict(card)}


def main() -> int:
    arguments = _parser().parse_args()
    result = asyncio.run(
        _snapshot(arguments) if arguments.action == "snapshot" else _move(arguments)
    )
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
