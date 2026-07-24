from __future__ import annotations

import asyncio

from client_surfaces.operator_tui.dashboard_http_adapter import DashboardHubAdapter
from client_surfaces.operator_tui.dashboard_surfaces import DashboardFeatureFlags
from client_surfaces.operator_tui.ops_api_client import OpsApiClient


def test_atomic_snapshot_path_does_not_call_legacy_board_or_cards(monkeypatch) -> None:
    requests: list[str] = []

    def request_json(self, method, path, *, payload=None, timeout=5.0):
        requests.append(path)
        if path == "/config/features/v1":
            return {
                "data": {
                    "schema": "ananta.dashboard-feature-flags.v1",
                    "features": {
                        "tui_kanban": True,
                        "tui_model_menu": False,
                    },
                }
            }
        if path == "/api/v1/kanban/boards/hub/snapshot":
            return {
                "data": {
                    "schema_version": "kanban.snapshot.v1",
                    "board": {
                        "schema_version": "kanban.v1",
                        "id": "hub",
                        "name": "Hub",
                        "scope_type": "hub",
                        "scope_id": None,
                        "revision": "board-r7",
                        "card_count": 1,
                        "capabilities": ["kanban.read"],
                        "columns": [
                            {
                                "id": "todo",
                                "title": "To do",
                                "statuses": ["todo"],
                                "card_count": 1,
                            }
                        ],
                    },
                    "cards": [
                        {
                            "schema_version": "kanban.v1",
                            "id": "TASK-1",
                            "board_id": "hub",
                            "title": "Atomic snapshot",
                            "description": "",
                            "status": "todo",
                            "column_id": "todo",
                            "position": 0,
                            "revision": 3,
                            "priority": "Medium",
                            "assignee": None,
                            "labels": [],
                            "blocked": False,
                            "dependencies": [],
                        }
                    ],
                    "event_sequence": 7,
                }
            }
        raise AssertionError(f"legacy endpoint called: {path}")

    monkeypatch.setattr(OpsApiClient, "request_json", request_json)
    adapter = DashboardHubAdapter(
        endpoint="http://hub.test",
        token="header.payload.signature",
        local_flags=DashboardFeatureFlags(kanban=True, models=False),
    )

    snapshot = asyncio.run(adapter.fetch_board())

    assert snapshot["board_id"] == "hub"
    assert snapshot["event_sequence"] == 7
    assert snapshot["columns"][0]["tasks"][0]["id"] == "TASK-1"
    assert requests == [
        "/config/features/v1",
        "/api/v1/kanban/boards/hub/snapshot",
    ]
