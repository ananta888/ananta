"""AFH-T011: WorkerOutputCollectorService tests."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from agent.services.worker_output_collector_service import get_worker_output_collector_service
from worker.core.artifact_manifest import build_artifact_manifest, write_manifest


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _write_fibonacci_manifest(workspace: Path, execution_id: str = "exec-1") -> Path:
    files = {
        "app.py": "from flask import Flask\napp = Flask(__name__)\n",
        "requirements.txt": "flask>=2.0\n",
        "README.md": "# Fibonacci\n",
    }
    for name, content in files.items():
        (workspace / name).write_text(content, encoding="utf-8")

    artifacts = [
        {
            "artifact_id": f"art-{i}",
            "kind": "generated_file",
            "relative_path": name,
            "content_hash": _sha256(content),
            "size_bytes": len(content.encode()),
            "required": True,
            "verification_status": "pending",
            "metadata": {},
        }
        for i, (name, content) in enumerate(files.items())
    ]
    manifest = build_artifact_manifest(
        goal_id="g1", task_id="t1", execution_id=execution_id, trace_id="tr-1",
        workspace_root=workspace, worker_id="worker-1", artifacts=artifacts,
    )
    manifest_dir = workspace / ".ananta" / "handoff" / execution_id
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "artifact_manifest.v1.json"
    write_manifest(manifest, manifest_path)
    return manifest_path


class TestWorkerOutputCollectorService:
    def test_collects_valid_manifest(self, workspace: Path) -> None:
        _write_fibonacci_manifest(workspace)
        svc = get_worker_output_collector_service()
        result = svc.collect(
            task_id="t1", goal_id="g1", execution_id="exec-1", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-1/artifact_manifest.v1.json",
        )
        assert result["manifest_valid"]
        assert len(result["artifacts"]) == 3
        assert not result["synthesized"]

    def test_missing_manifest_returns_invalid(self, workspace: Path) -> None:
        svc = get_worker_output_collector_service()
        result = svc.collect(
            task_id="t1", goal_id="g1", execution_id="exec-missing", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-missing/artifact_manifest.v1.json",
        )
        assert not result["manifest_valid"]
        assert any("missing" in e for e in result["errors"])

    def test_synthesized_fallback_when_allowed(self, workspace: Path) -> None:
        (workspace / "app.py").write_text("code", encoding="utf-8")
        svc = get_worker_output_collector_service()
        _, before_snap = {}, {}
        _, after_snap = {}, {"app.py": _sha256("code")}
        result = svc.collect(
            task_id="t1", goal_id="g1", execution_id="exec-synth", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-synth/artifact_manifest.v1.json",
            allow_synthesized_fallback=True,
            before_snapshot_id="before",
            before_snapshot={},
            after_snapshot_id="after",
            after_snapshot={"app.py": _sha256("code")},
        )
        assert result["synthesized"]

    def test_manifest_path_escaping_workspace_rejected(self, workspace: Path, tmp_path: Path) -> None:
        svc = get_worker_output_collector_service()
        result = svc.collect(
            task_id="t1", goal_id="g1", execution_id="exec-bad", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path="../outside/manifest.json",
        )
        assert not result["manifest_valid"]
        assert any("escapes_workspace" in e or "path" in e for e in result["errors"])
