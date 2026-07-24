from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from agent.db_models import TaskDB
from client_surfaces.operator_tui.dashboard_http_adapter import (
    DashboardHubAdapter,
)
from client_surfaces.operator_tui.dashboard_surfaces import (
    DashboardFeatureFlags,
    DashboardSurfaceController,
    RevisionConflict,
)


FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "kanban_model_dashboard"
    / "kanban-model-dashboard.v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _enable_tui_kanban(app) -> None:
    app.config["KANBAN_API_ENABLED"] = True
    app.config["KANBAN_WRITE_ENABLED"] = True
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG", {}) or {}),
        "feature_tui_kanban_enabled": True,
    }


def _start_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_shared_fixture_drives_real_flask_tui_conflict_and_snapshot_reload(
    app,
    db_session,
    admin_token,
) -> None:
    fixture = _fixture()
    fixture_card = fixture["card"]
    fixture_error = fixture["error"]
    _enable_tui_kanban(app)
    timestamp = datetime.fromisoformat(
        fixture_card["updated_at"].replace("Z", "+00:00")
    ).timestamp()
    db_session.add(
        TaskDB(
            id=fixture_card["id"],
            title=fixture_card["title"],
            description=fixture_card["description"],
            status=fixture_card["status"],
            priority=fixture_card["priority"],
            created_at=timestamp,
            updated_at=timestamp,
            kanban_position=fixture_card["position"],
            kanban_revision=fixture_card["revision"],
            history=[],
            depends_on=[],
        )
    )
    db_session.commit()

    server, thread = _start_server(app)
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        adapter = DashboardHubAdapter(
            endpoint=endpoint,
            token=admin_token,
            local_flags=DashboardFeatureFlags(kanban=True, models=False),
            idempotency_key_factory=lambda: "shared-fixture-command",
        )
        controller = DashboardSurfaceController(
            kanban_port=adapter,
            model_catalog_port=adapter,
            flags=DashboardFeatureFlags(kanban=True, models=False),
        )

        loaded = asyncio.run(controller.load_kanban())
        initial = loaded.payload["items"][0]
        assert initial["id"] == fixture_card["id"]
        assert initial["revision"] == fixture_card["revision"]

        persisted = db_session.get(TaskDB, fixture_card["id"])
        assert persisted is not None
        persisted.kanban_revision = fixture_error["body"]["error"]["details"][
            "current_revision"
        ]
        persisted.updated_at = timestamp + 1
        db_session.add(persisted)
        db_session.commit()

        with pytest.raises(RevisionConflict) as conflict:
            asyncio.run(
                adapter.move_task(
                    fixture_card["id"],
                    target_status="completed",
                    target_position=0,
                    expected_revision=fixture_card["revision"],
                )
            )
        assert fixture_error["http_status"] == 409
        assert str(conflict.value) == fixture_error["body"]["error"]["code"]
        assert conflict.value.current_revision == fixture_error["body"]["error"][
            "details"
        ]["current_revision"]

        controller.select("kanban", 0)
        reloaded = asyncio.run(controller.move_selected("completed"))
        current = reloaded.payload["items"][0]
        assert reloaded.payload["revision_conflict"] is True
        assert current["id"] == fixture_card["id"]
        assert current["revision"] == fixture_error["body"]["error"]["details"][
            "current_revision"
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
