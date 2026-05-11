"""AFH-T005: ArtifactManifest v1 schema and contract tests."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from agent.services.artifact_manifest_service import get_artifact_manifest_service
from worker.core.artifact_manifest import build_artifact_entry, build_artifact_manifest, write_manifest, load_manifest


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def fibonacci_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "fibonacci_project"
    ws.mkdir()
    (ws / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8")
    (ws / "requirements.txt").write_text("flask>=2.0\n", encoding="utf-8")
    (ws / "README.md").write_text("# Fibonacci Flask\n", encoding="utf-8")
    return ws


class TestArtifactManifestBuild:
    def test_build_manifest_valid_structure(self, fibonacci_workspace: Path) -> None:
        artifacts = []
        for name in ["app.py", "requirements.txt", "README.md"]:
            entry = build_artifact_entry(
                workspace_root=fibonacci_workspace,
                relative_path=name,
                kind="generated_file",
                operation="created",
                required=True,
            )
            artifacts.append(entry)
        manifest = build_artifact_manifest(
            goal_id="goal-fib",
            task_id="task-fib",
            execution_id="exec-fib",
            trace_id="tr-fib",
            workspace_root=fibonacci_workspace,
            worker_id="worker-1",
            artifacts=artifacts,
            summary="Fibonacci project created",
        )
        assert manifest["schema"] == "artifact_manifest.v1"
        assert manifest["goal_id"] == "goal-fib"
        assert manifest["task_id"] == "task-fib"
        assert len(manifest["artifacts"]) == 3
        assert manifest["synthesized"] is False

    def test_write_and_load_manifest(self, fibonacci_workspace: Path, tmp_path: Path) -> None:
        entry = build_artifact_entry(
            workspace_root=fibonacci_workspace,
            relative_path="app.py",
            kind="generated_file",
        )
        manifest = build_artifact_manifest(
            goal_id="g", task_id="t", execution_id="e", trace_id="tr",
            workspace_root=fibonacci_workspace, worker_id="w", artifacts=[entry],
        )
        out = tmp_path / "manifest.json"
        write_manifest(manifest, out)
        loaded = load_manifest(out)
        assert loaded["schema"] == "artifact_manifest.v1"
        assert len(loaded["artifacts"]) == 1

    def test_artifact_entry_rejects_traversal(self, fibonacci_workspace: Path) -> None:
        with pytest.raises(ValueError):
            build_artifact_entry(
                workspace_root=fibonacci_workspace,
                relative_path="../outside.txt",
            )

    def test_artifact_entry_rejects_absolute_path(self, fibonacci_workspace: Path) -> None:
        with pytest.raises(ValueError):
            build_artifact_entry(
                workspace_root=fibonacci_workspace,
                relative_path="/etc/passwd",
            )

    def test_synthesized_manifest_marked_correctly(self, fibonacci_workspace: Path) -> None:
        manifest = build_artifact_manifest(
            goal_id="g", task_id="t", execution_id="e", trace_id="tr",
            workspace_root=fibonacci_workspace, worker_id="hub-synth", artifacts=[],
            synthesized=True,
        )
        assert manifest["synthesized"] is True


class TestArtifactManifestValidation:
    def test_valid_manifest_passes(self, fibonacci_workspace: Path) -> None:
        svc = get_artifact_manifest_service()
        entry = build_artifact_entry(
            workspace_root=fibonacci_workspace, relative_path="app.py",
            kind="generated_file", required=True,
        )
        manifest = build_artifact_manifest(
            goal_id="g", task_id="t", execution_id="e", trace_id="tr",
            workspace_root=fibonacci_workspace, worker_id="w", artifacts=[entry],
        )
        result = svc.validate_manifest(manifest, workspace_root=fibonacci_workspace)
        assert result["valid"], f"Valid manifest must pass validation: {result['errors']}"

    def test_missing_hash_fails(self, fibonacci_workspace: Path) -> None:
        svc = get_artifact_manifest_service()
        manifest = {
            "schema": "artifact_manifest.v1",
            "manifest_id": "m1", "goal_id": "g", "task_id": "t",
            "execution_id": "e", "trace_id": "tr",
            "workspace_root_ref": "ref", "produced_by_worker_id": "w",
            "produced_at": 1.0, "synthesized": False,
            "artifacts": [{
                "artifact_id": "a1", "kind": "generated_file",
                "relative_path": "app.py",
                "content_hash": "",  # missing
                "size_bytes": 0,
            }],
        }
        result = svc.validate_manifest(manifest, workspace_root=fibonacci_workspace)
        assert not result["valid"]

    def test_path_traversal_fails(self, fibonacci_workspace: Path) -> None:
        svc = get_artifact_manifest_service()
        manifest = {
            "schema": "artifact_manifest.v1",
            "manifest_id": "m2", "goal_id": "g", "task_id": "t",
            "execution_id": "e", "trace_id": "tr",
            "workspace_root_ref": "ref", "produced_by_worker_id": "w",
            "produced_at": 1.0, "synthesized": False,
            "artifacts": [{
                "artifact_id": "a2", "kind": "generated_file",
                "relative_path": "../../etc/passwd",
                "content_hash": "a" * 64, "size_bytes": 0,
            }],
        }
        result = svc.validate_manifest(manifest, workspace_root=fibonacci_workspace)
        assert not result["valid"]
        assert any("path_traversal" in e or "escapes_workspace" in e for e in result["errors"])
