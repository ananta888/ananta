from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from agent.db_models import TaskDB


REPOSITORY_ROOT = Path(__file__).parents[3]
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "kanban_model_dashboard"
    / "kanban-model-dashboard.v1.json"
)
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend-angular"
TUI_PROBE_PATH = (
    REPOSITORY_ROOT
    / "tests"
    / "client_surfaces"
    / "operator_tui"
    / "kanban_cross_surface_probe.py"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _enable_cross_surface_kanban(app) -> None:
    app.config["KANBAN_API_ENABLED"] = True
    app.config["KANBAN_WRITE_ENABLED"] = True
    app.config["AGENT_CONFIG"] = {
        **dict(app.config.get("AGENT_CONFIG", {}) or {}),
        "feature_angular_kanban_enabled": True,
        "feature_tui_kanban_enabled": True,
    }


@pytest.mark.skipif(
    os.getenv("RUN_KANBAN_CROSS_SURFACE_E2E") != "1",
    reason="set RUN_KANBAN_CROSS_SURFACE_E2E=1 for the browser/TUI live-Hub test",
)
def test_angular_and_tui_share_real_hub_projection_bidirectionally(
    app,
    db_session,
    admin_token,
    tmp_path,
) -> None:
    fixture = _fixture()
    fixture_card = fixture["card"]
    _enable_cross_surface_kanban(app)
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
            worker_execution_context={
                "kanban_labels": list(fixture_card["labels"]),
            },
            history=[],
            depends_on=[],
        )
    )
    db_session.commit()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        hub_url = f"http://127.0.0.1:{server.server_port}"
        tui_preflight = subprocess.run(
            [
                sys.executable,
                str(TUI_PROBE_PATH),
                "--endpoint",
                hub_url,
                "--token",
                admin_token,
                "snapshot",
                "--task-id",
                fixture_card["id"],
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert tui_preflight.returncode == 0, (
            "TUI live-Hub preflight failed\n"
            f"stdout:\n{tui_preflight.stdout}\n"
            f"stderr:\n{tui_preflight.stderr}"
        )
        tui_snapshot = json.loads(tui_preflight.stdout)
        assert tui_snapshot["ok"] is True
        assert tui_snapshot["card"] == {
            "id": fixture_card["id"],
            "title": fixture_card["title"],
            "description": fixture_card["description"],
            "status": fixture_card["status"],
            "priority": fixture_card["priority"],
            "assignee_id": "",
            "labels": fixture_card["labels"],
            "blocked": fixture_card["blocked"],
            "dependencies": fixture_card["dependencies"],
            "revision": fixture_card["revision"],
            "column_id": fixture_card["column_id"],
        }

        frontend_port = _available_port()
        environment = {
            **os.environ,
            "CROSS_SURFACE_FRONTEND_PORT": str(frontend_port),
            "CROSS_SURFACE_HUB_URL": hub_url,
            "CROSS_SURFACE_TOKEN": admin_token,
            "CROSS_SURFACE_PYTHON": sys.executable,
            "CROSS_SURFACE_REPOSITORY_ROOT": str(REPOSITORY_ROOT),
            "CROSS_SURFACE_OUTPUT_DIR": str(tmp_path / "playwright-output"),
            "E2E_PORT": str(frontend_port),
        }
        completed = subprocess.run(
            [
                "npx",
                "playwright",
                "test",
                "tests/kanban-cross-surface-live-hub.spec.ts",
                "--config=playwright.cross-surface.config.ts",
                "--workers=1",
                "--reporter=line",
            ],
            cwd=FRONTEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, (
            "cross-surface Playwright failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )

        db_session.expire_all()
        persisted = db_session.get(TaskDB, fixture_card["id"])
        assert persisted is not None
        assert persisted.status == "todo"
        assert persisted.kanban_revision == fixture_card["revision"] + 2
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
