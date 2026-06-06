from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from client_surfaces.operator_tui.tools.git_read_tool import GitReadTool
from client_surfaces.operator_tui.tools.todo_read_tool import TodoReadTool


def _init_repo(path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable not available")
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, check=True, capture_output=True)


def test_git_read_tool_status_and_recent_commits(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "changed.txt").write_text("changed\n", encoding="utf-8")

    tool = GitReadTool(tmp_path)
    status = tool.status()
    commits = tool.recent_commits(limit=3)

    assert status.ok
    assert status.data["changed_count"] == 1
    assert status.data["changed_files"][0]["path"] == "changed.txt"
    assert commits.ok
    assert commits.data["count"] == 1
    assert commits.data["commits"][0]["subject"] == "Initial commit"


def test_todo_read_tool_lists_active_tracks_and_finds_task(tmp_path: Path) -> None:
    todos = tmp_path / "todos"
    todos.mkdir()
    payload = {
        "track": "example",
        "tasks": [
            {
                "id": "EX-001",
                "title": "Do the thing",
                "status": "todo",
                "priority": "P0",
                "risk": "medium",
            },
            {
                "id": "EX-002",
                "title": "Done thing",
                "status": "done",
                "priority": "P1",
                "risk": "low",
            },
        ],
    }
    (todos / "todo.example.json").write_text(json.dumps(payload), encoding="utf-8")

    tool = TodoReadTool(tmp_path)
    active = tool.list_active_tracks()
    listed = tool.list_todos("todos/todo.example.json")
    found = tool.find_task_by_id("EX-001")

    assert active.ok
    assert active.data["tracks"][0]["track"] == "example"
    assert listed.ok
    assert listed.data["count"] == 2
    assert found.ok
    assert found.data["task"]["title"] == "Do the thing"


def test_todo_read_tool_blocks_paths_outside_todos(tmp_path: Path) -> None:
    (tmp_path / "todos").mkdir()
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    result = TodoReadTool(tmp_path).list_todos("outside.json")

    assert result.ok is False
    assert result.error == "todo_path_denied"
