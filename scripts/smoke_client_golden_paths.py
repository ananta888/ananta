from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from client_surfaces.common.client_api import AnantaApiClient
from client_surfaces.common.profile_auth import build_client_profile
from client_surfaces.tui_runtime.ananta_tui.fixture_transport import build_fixture_transport

ROOT = Path(__file__).resolve().parents[1]
STATUS_FILE = ROOT / "data" / "client_surface_runtime_status.json"


def _run_tui_goal_path() -> dict[str, Any]:
    client = AnantaApiClient(
        build_client_profile({"profile_id": "golden-tui", "base_url": "http://localhost:8080"}),
        transport=build_fixture_transport(),
    )
    context_payload = {
        "schema": "client_bounded_context_payload_v1",
        "file_path": "/workspace/src/main.py",
        "project_root": "/workspace",
        "selection_text": "def main(): pass",
    }
    goal_response = client.submit_goal("Golden path goal from TUI fixture", context_payload)
    tasks_response = client.list_tasks()
    artifacts_response = client.list_artifacts()
    ok = (
        goal_response.ok
        and isinstance(goal_response.data, dict)
        and bool(goal_response.data.get("task_id"))
        and tasks_response.ok
        and bool((tasks_response.data or {}).get("items"))
        and artifacts_response.ok
        and bool((artifacts_response.data or {}).get("items"))
    )
    return {
        "path": "tui",
        "ok": ok,
        "goal_response": {
            "state": goal_response.state,
            "status_code": goal_response.status_code,
            "data": goal_response.data,
        },
        "tasks_count": len((tasks_response.data or {}).get("items") or []),
        "artifacts_count": len((artifacts_response.data or {}).get("items") or []),
    }


def _run_nvim_goal_path() -> dict[str, Any]:
    env = os.environ.copy()
    env["ANANTA_NVIM_FIXTURE"] = "1"
    command = [
        sys.executable,
        "-m",
        "client_surfaces.nvim_runtime.ananta_bridge",
        "--command",
        "goal_submit",
        "--goal-text",
        "Golden path goal from Neovim fixture",
        "--base-url",
        "http://localhost:8080",
        "--file-path",
        "/workspace/src/main.py",
        "--project-root",
        "/workspace",
        "--selection-text",
        "print('golden-path')",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env, cwd=str(ROOT))
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    response = payload.get("response") if isinstance(payload, dict) else {}
    response_data = response.get("data") if isinstance(response, dict) else {}
    ok = (
        result.returncode == 0
        and isinstance(response, dict)
        and response.get("ok") is True
        and isinstance(response_data, dict)
        and bool(response_data.get("goal_id"))
        and bool(response_data.get("task_id"))
    )
    return {
        "path": "nvim",
        "ok": ok,
        "returncode": result.returncode,
        "response": response if isinstance(response, dict) else {},
        "stderr": result.stderr.strip(),
    }


def _run_eclipse_path_check() -> dict[str, Any]:
    status_payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    status = str(status_payload.get("surface_status", {}).get("eclipse_plugin", "")).strip().lower()
    if status in {"runtime_mvp", "runtime_complete"}:
        return {
            "path": "eclipse",
            "ok": False,
            "status": status,
            "detail": "eclipse runtime claimed as runnable but no runtime smoke path is implemented in this track",
        }
    return {
        "path": "eclipse",
        "ok": True,
        "status": status or "missing",
        "detail": "runtime not claimed; manual foundation checklist remains documentation-only",
    }


def run_golden_paths_once() -> tuple[bool, dict[str, Any]]:
    results = [
        _run_tui_goal_path(),
        _run_nvim_goal_path(),
        _run_eclipse_path_check(),
    ]
    required_failures = [result["path"] for result in results if result["ok"] is not True]
    payload = {
        "schema": "client_surface_golden_path_smoke_v1",
        "results": results,
        "ok": not required_failures,
        "failures": required_failures,
    }
    return not required_failures, payload


def main() -> int:
    ok, payload = run_golden_paths_once()
    if ok:
        print("client-golden-path-smoke-ok")
    else:
        print("client-golden-path-smoke-failed")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
