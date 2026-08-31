from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from client_surfaces.operator_tui.dashboard_http_adapter import (
    DashboardHttpError,
    DashboardHubAdapter,
    DashboardPermissionError,
)
from client_surfaces.operator_tui.dashboard_surfaces import (
    DashboardFeatureFlags,
    DashboardSurfaceController,
    RevisionConflict,
)


class _HubState:
    def __init__(self) -> None:
        self.features = {"tui_kanban": True, "tui_model_menu": True}
        self.requests: list[dict] = []
        self.command_status: dict[str, int] = {}
        self.command_error: dict[str, str] = {}
        self.board_reads = 0
        self.board = {
            "schema_version": "kanban.v1",
            "id": "hub",
            "name": "Hub",
            "scope_type": "hub",
            "scope_id": None,
            "revision": "board-r1",
            "card_count": 2,
            "capabilities": ["kanban.read", "kanban.write"],
            "columns": [
                {
                    "id": "todo",
                    "title": "Todo",
                    "statuses": ["pending"],
                    "card_count": 1,
                },
                {
                    "id": "completed",
                    "title": "Completed",
                    "statuses": ["completed"],
                    "card_count": 1,
                },
            ],
        }
        self.cards = [
            {
                "schema_version": "kanban.v1",
                "id": "TASK-1",
                "board_id": "hub",
                "title": "First",
                "description": "",
                "status": "pending",
                "column_id": "todo",
                "position": 0,
                "revision": 4,
                "priority": "P0",
                "assignee": {"id": "agent-1", "name": "Agent", "url": "http://worker"},
                "labels": ["core"],
                "blocked": False,
                "dependencies": [],
                "comment_count": 0,
                "activity_count": 1,
                "created_at": "2026-07-23T00:00:00Z",
                "updated_at": "2026-07-23T00:00:00Z",
            },
            {
                "schema_version": "kanban.v1",
                "id": "TASK-2",
                "board_id": "hub",
                "title": "Second",
                "description": "",
                "status": "completed",
                "column_id": "completed",
                "position": 0,
                "revision": 9,
                "priority": "P1",
                "assignee": None,
                "labels": [],
                "blocked": False,
                "dependencies": [],
                "comment_count": 0,
                "activity_count": 1,
                "created_at": "2026-07-23T00:00:00Z",
                "updated_at": "2026-07-23T00:00:00Z",
            },
        ]
        self.catalog = {
            "schema": "ananta.model-catalog.v1",
            "default_selection": {
                "schema": "ananta.model-default-selection.v1",
                "provider_id": "local",
                "model_id": "safe-model",
            },
            "models": [
                {
                    "schema": "ananta.model-summary.v1",
                    "provider_id": "local",
                    "runtime": "local",
                    "model_id": "safe-model",
                    "display_name": "Safe Model",
                    "availability": "available",
                    "loaded": True,
                    "context_window": 8192,
                    "quantization": "Q4",
                    "capabilities": ["chat"],
                    "health": "healthy",
                    "is_default": True,
                }
            ],
            "provider_failures": [
                {"provider_id": "broken", "reason_code": "unavailable"}
            ],
        }


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, format, *args) -> None:
        return

    @property
    def state(self) -> _HubState:
        return self.server.state  # type: ignore[attr-defined]

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        body = self._body() if method == "POST" else {}
        self.state.requests.append(
            {
                "method": method,
                "path": parsed.path,
                "query": urllib.parse.parse_qs(parsed.query),
                "body": body,
                "authorization": self.headers.get("Authorization"),
            }
        )
        if self.headers.get("Authorization") != "Bearer header.payload.signature":
            self._send(401, {"message": "authentication_required"})
            return
        if parsed.path == "/config/features/v1" and method == "GET":
            self._send(
                200,
                {
                    "data": {
                        "schema": "ananta.dashboard-feature-flags.v1",
                        "features": {
                            "angular_kanban": False,
                            "angular_model_dashboard": False,
                            **self.state.features,
                        },
                    }
                },
            )
            return
        if parsed.path == "/api/v1/kanban/boards/hub" and method == "GET":
            self.state.board_reads += 1
            self._send(200, {"data": self.state.board})
            return
        if parsed.path == "/api/v1/kanban/boards/hub/cards" and method == "GET":
            self._send(
                200,
                {
                    "data": {
                        "schema_version": "kanban.v1",
                        "board_id": "hub",
                        "board_revision": self.state.board["revision"],
                        "items": self.state.cards,
                        "next_cursor": None,
                    }
                },
            )
            return
        if parsed.path.startswith("/api/v1/kanban/cards/") and method == "POST":
            command = parsed.path.rsplit("/", 1)[-1]
            status = self.state.command_status.get(command, 200)
            if status != 200:
                code = self.state.command_error.get(command, "kanban_revision_conflict")
                self._send(
                    status,
                    {
                        "error": {
                            "code": code,
                            "message": code,
                            "details": {"current_revision": 10},
                        }
                    },
                )
                return
            self._send(200, {"data": self.state.cards[0]})
            return
        if parsed.path == "/models/catalog/v1" and method == "GET":
            self._send(200, {"data": self.state.catalog})
            return
        if parsed.path == "/models/catalog/v1/refresh" and method == "POST":
            self._send(200, {"data": self.state.catalog})
            return
        if parsed.path == "/models/default/v1" and method == "POST":
            self._send(
                200,
                {
                    "data": {
                        "schema": "ananta.model-default-selection.v1",
                        "provider_id": body.get("provider_id"),
                        "model_id": body.get("model_id"),
                    }
                },
            )
            return
        self._send(404, {"message": "not_found"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")


@pytest.fixture
def local_hub():
    state = _HubState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _adapter(endpoint: str, **kwargs) -> DashboardHubAdapter:
    return DashboardHubAdapter(
        endpoint=endpoint,
        token="header.payload.signature",
        local_flags=kwargs.pop(
            "local_flags",
            DashboardFeatureFlags(kanban=True, models=True),
        ),
        idempotency_key_factory=kwargs.pop(
            "idempotency_key_factory",
            lambda: "tui-test-key",
        ),
        **kwargs,
    )


def test_local_and_backend_flags_are_combined_fail_closed(local_hub) -> None:
    endpoint, state = local_hub
    disabled = _adapter(
        endpoint,
        local_flags=DashboardFeatureFlags(kanban=False, models=False),
    )
    with pytest.raises(DashboardHttpError, match="tui_kanban_disabled_local"):
        asyncio.run(disabled.fetch_board())
    assert state.requests == []

    state.features["tui_kanban"] = False
    enabled_locally = _adapter(endpoint)
    with pytest.raises(DashboardHttpError, match="tui_kanban_disabled_backend"):
        asyncio.run(enabled_locally.fetch_board())
    assert [request["path"] for request in state.requests] == ["/config/features/v1"]


def test_board_and_cards_are_composed_through_authenticated_hub_calls(local_hub) -> None:
    endpoint, state = local_hub
    board = asyncio.run(_adapter(endpoint).fetch_board())

    assert board["revision"] == "board-r1"
    assert [column["id"] for column in board["columns"]] == ["todo", "completed"]
    assert board["columns"][0]["tasks"][0]["id"] == "TASK-1"
    assert board["columns"][0]["tasks"][0]["assignee_id"] == "agent-1"
    assert all(
        request["authorization"] == "Bearer header.payload.signature"
        for request in state.requests
    )
    assert [request["path"] for request in state.requests] == [
        "/config/features/v1",
        "/api/v1/kanban/boards/hub/snapshot",
        "/api/v1/kanban/boards/hub",
        "/api/v1/kanban/boards/hub/cards",
    ]


def test_all_kanban_commands_send_revision_and_stable_contract(local_hub) -> None:
    endpoint, state = local_hub
    keys = iter(f"key-{index}" for index in range(5))
    adapter = _adapter(endpoint, idempotency_key_factory=lambda: next(keys))

    asyncio.run(
        adapter.move_task(
            "TASK-1",
            target_status="done",
            target_position=2,
            expected_revision=4,
        )
    )
    asyncio.run(adapter.assign_task("TASK-1", assignee_id="agent-2", expected_revision=5))
    asyncio.run(adapter.comment_task("TASK-1", body="note", expected_revision=6))
    asyncio.run(adapter.block_task("TASK-1", reason="dependency", expected_revision=7))
    asyncio.run(adapter.complete_task("TASK-1", expected_revision=8))

    commands = [
        request
        for request in state.requests
        if "/commands/" in request["path"]
    ]
    assert [request["path"].rsplit("/", 1)[-1] for request in commands] == [
        "move",
        "assign",
        "comment",
        "block",
        "complete",
    ]
    assert [request["body"]["expected_revision"] for request in commands] == [
        4,
        5,
        6,
        7,
        8,
    ]
    assert [request["body"]["idempotency_key"] for request in commands] == [
        "key-0",
        "key-1",
        "key-2",
        "key-3",
        "key-4",
    ]
    assert commands[0]["body"]["column_id"] == "completed"
    assert all(request["body"]["board_id"] == "hub" for request in commands)


def test_http_409_maps_to_controller_snapshot_reload(local_hub) -> None:
    endpoint, state = local_hub
    state.command_status["move"] = 409
    adapter = _adapter(endpoint)
    controller = DashboardSurfaceController(
        kanban_port=adapter,
        model_catalog_port=adapter,
        flags=DashboardFeatureFlags(kanban=True, models=True),
    )
    loaded = asyncio.run(controller.load_kanban())
    controller.select("kanban", 0)

    conflict = asyncio.run(controller.move_selected("completed"))

    assert loaded.payload["items"][0]["revision"] == 4
    assert conflict.payload["revision_conflict"] is True
    assert "Snapshot neu geladen" in conflict.message
    assert state.board_reads == 2


def test_stable_permission_and_backend_error_codes_are_preserved(local_hub) -> None:
    endpoint, state = local_hub
    state.command_status["complete"] = 403
    state.command_error["complete"] = "kanban_write_forbidden"
    adapter = _adapter(endpoint)

    with pytest.raises(DashboardPermissionError) as denied:
        asyncio.run(adapter.complete_task("TASK-1", expected_revision=4))

    assert denied.value.code == "kanban_write_forbidden"
    assert denied.value.status_code == 403


def test_model_catalog_refresh_and_default_remain_hub_relative(local_hub) -> None:
    endpoint, state = local_hub
    adapter = _adapter(endpoint)
    catalog = asyncio.run(adapter.fetch_catalog())

    assert catalog["models"] == [
        {
            "id": "safe-model",
            "provider": "local",
            "runtime": "local",
            "available": True,
            "healthy": True,
            "loaded": True,
            "context_window": 8192,
            "quantization": "Q4",
            "capabilities": ["chat"],
            "default": True,
            "revision": catalog["revision"],
        }
    ]
    assert catalog["provider_errors"] == [
        {"provider": "broken", "code": "unavailable"}
    ]

    asyncio.run(adapter.refresh_catalog())
    selected = asyncio.run(
        adapter.set_default(
            "safe-model",
            expected_revision=catalog["revision"],
        )
    )
    assert selected["model_id"] == "safe-model"
    default_request = next(
        request for request in state.requests if request["path"] == "/models/default/v1"
    )
    assert default_request["body"] == {
        "schema": "ananta.model-default-selection-command.v1",
        "provider_id": "local",
        "model_id": "safe-model",
    }
    assert all(
        request["path"].startswith("/")
        and "://" not in request["path"]
        for request in state.requests
    )


def test_model_catalog_v2_preserves_capability_provenance_for_tui() -> None:
    adapter = _adapter("http://127.0.0.1:1")
    mapped = adapter._map_catalog(
        {
            "schema": "ananta.model-catalog.v2",
            "catalog_revision": 7,
            "models": [
                {
                    "provider_id": "ollama",
                    "model_id": "local-model",
                    "runtime": "local",
                    "availability": "available",
                    "health": "healthy",
                    "loaded": True,
                    "context_window": 8192,
                    "quantization": "q4",
                    "capabilities": [
                        {
                            "capability_id": "tools",
                            "value": "supported",
                            "evidence": "detected",
                            "source_id": "local.runtime.capabilities",
                        }
                    ],
                    "metadata_facts": [
                        {
                            "fact_id": "capability.tools.source",
                            "value": "runtime_reported",
                            "evidence": "detected",
                            "source_id": "local.runtime.capabilities",
                        }
                    ],
                    "conflicts": ["template_conflict"],
                }
            ],
            "sources": [],
            "partial": False,
        }
    )

    assert mapped["models"][0]["capabilities"] == ["tools"]
    assert mapped["models"][0]["capability_sources"] == [
        "capability.tools.source:runtime_reported"
    ]
    assert mapped["models"][0]["conflicts"] == ["template_conflict"]


def test_stale_model_revision_fails_before_default_write(local_hub) -> None:
    endpoint, state = local_hub
    adapter = _adapter(endpoint)

    with pytest.raises(RevisionConflict, match="model_catalog_revision_conflict"):
        asyncio.run(
            adapter.set_default(
                "safe-model",
                expected_revision="stale",
            )
        )

    assert not any(
        request["path"] == "/models/default/v1"
        for request in state.requests
    )
