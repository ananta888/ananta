"""AFH-T007: WorkerHandoffBundle v1 schema and generation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.core.worker_handoff_bundle import WorkerHandoffBundle, ExpectedArtifact


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


class TestWorkerHandoffBundle:
    def test_bundle_materialize_creates_files(self, workspace: Path) -> None:
        bundle = WorkerHandoffBundle.build(
            task_id="t1",
            goal_id="g1",
            execution_id="exec-1",
            trace_id="tr-1",
            workspace_root=workspace,
            expected_artifacts=[
                {"kind": "generated_file", "relative_path": "app.py", "required": True},
            ],
        )
        handoff_dir = workspace / ".ananta" / "handoff" / "exec-1"
        bundle.materialize(handoff_dir, instructions="Write app.py")

        assert (handoff_dir / "worker_handoff.json").exists()
        assert (handoff_dir / "instructions.md").exists()
        assert (handoff_dir / "expected_artifacts.json").exists()

    def test_instructions_include_manifest_requirement(self, workspace: Path) -> None:
        bundle = WorkerHandoffBundle.build(
            task_id="t1", goal_id="g1", execution_id="exec-2",
            trace_id="tr-2", workspace_root=workspace,
        )
        handoff_dir = workspace / ".ananta" / "handoff" / "exec-2"
        bundle.materialize(handoff_dir, instructions="Do the task.")

        instructions = (handoff_dir / "instructions.md").read_text(encoding="utf-8")
        assert "artifact_manifest.v1" in instructions, (
            "Instructions must mention the artifact manifest requirement"
        )
        assert "artifact_manifest.v1.json" in instructions

    def test_bundle_manifest_output_path_is_workspace_relative(self, workspace: Path) -> None:
        bundle = WorkerHandoffBundle.build(
            task_id="t1", goal_id="g1", execution_id="exec-3",
            trace_id="tr-3", workspace_root=workspace,
        )
        assert not bundle.manifest_output_path.startswith("/"), (
            "manifest_output_path must be relative, not absolute"
        )
        assert ".." not in bundle.manifest_output_path.split("/"), (
            "manifest_output_path must not contain path traversal"
        )

    def test_bundle_to_dict_schema(self, workspace: Path) -> None:
        bundle = WorkerHandoffBundle.build(
            task_id="t1", goal_id="g1", execution_id="exec-4",
            trace_id="tr-4", workspace_root=workspace,
        )
        d = bundle.to_dict()
        assert d["schema"] == "worker_handoff_bundle.v1"
        assert d["task_id"] == "t1"
        assert d["manifest_output_path"]

    def test_handoff_bundle_json_loadable(self, workspace: Path) -> None:
        bundle = WorkerHandoffBundle.build(
            task_id="t1", goal_id="g1", execution_id="exec-5",
            trace_id="tr-5", workspace_root=workspace,
        )
        handoff_dir = workspace / ".ananta" / "handoff" / "exec-5"
        bundle.materialize(handoff_dir, instructions="Test")
        data = json.loads((handoff_dir / "worker_handoff.json").read_text(encoding="utf-8"))
        assert data["schema"] == "worker_handoff_bundle.v1"
