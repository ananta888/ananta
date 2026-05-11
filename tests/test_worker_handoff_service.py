"""AFH-T010: WorkerHandoffService filesystem tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.worker_handoff_service import get_worker_handoff_service


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


class TestWorkerHandoffService:
    def test_creates_handoff_directory(self, workspace: Path) -> None:
        svc = get_worker_handoff_service()
        result = svc.create_handoff(
            task_id="t1", goal_id="g1", execution_id="exec-1", trace_id="tr-1",
            workspace_root=workspace, instructions="Do the task.",
        )
        handoff_dir = Path(result["handoff_dir"])
        assert handoff_dir.exists()
        assert handoff_dir.is_dir()

    def test_creates_worker_handoff_json(self, workspace: Path) -> None:
        svc = get_worker_handoff_service()
        result = svc.create_handoff(
            task_id="t1", goal_id="g1", execution_id="exec-2", trace_id="tr-2",
            workspace_root=workspace, instructions="Instructions here.",
        )
        bundle_path = Path(result["bundle_path"])
        assert bundle_path.exists()
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert data["schema"] == "worker_handoff_bundle.v1"

    def test_creates_instructions_md(self, workspace: Path) -> None:
        svc = get_worker_handoff_service()
        result = svc.create_handoff(
            task_id="t1", goal_id="g1", execution_id="exec-3", trace_id="tr-3",
            workspace_root=workspace, instructions="Write the Fibonacci app.",
        )
        instructions_path = Path(result["instructions_path"])
        assert instructions_path.exists()
        content = instructions_path.read_text(encoding="utf-8")
        assert "Write the Fibonacci app." in content
        assert "artifact_manifest" in content

    def test_manifest_output_path_inside_workspace(self, workspace: Path) -> None:
        svc = get_worker_handoff_service()
        result = svc.create_handoff(
            task_id="t1", goal_id="g1", execution_id="exec-4", trace_id="tr-4",
            workspace_root=workspace, instructions="Task.",
        )
        manifest_path = Path(result["manifest_output_path"])
        assert manifest_path.is_relative_to(workspace.resolve()), (
            f"Manifest output path must be inside workspace: {manifest_path}"
        )

    def test_all_handoff_paths_workspace_bound(self, workspace: Path) -> None:
        svc = get_worker_handoff_service()
        result = svc.create_handoff(
            task_id="t1", goal_id="g1", execution_id="exec-5", trace_id="tr-5",
            workspace_root=workspace, instructions="Task.",
        )
        for key in ("handoff_dir", "bundle_path", "manifest_output_path", "instructions_path"):
            path = Path(result[key])
            assert path.is_relative_to(workspace.resolve()), (
                f"{key}={result[key]!r} must be inside workspace"
            )

    def test_invalid_workspace_raises(self, tmp_path: Path) -> None:
        svc = get_worker_handoff_service()
        with pytest.raises(ValueError, match="workspace_root"):
            svc.create_handoff(
                task_id="t1", goal_id="g1", execution_id="exec-6", trace_id="tr-6",
                workspace_root=tmp_path / "nonexistent",
                instructions="Task.",
            )
