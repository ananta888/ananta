"""AFH-T016: ArtifactReconciliationService dry-run and apply tests."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from agent.services.artifact_reconciliation_service import get_artifact_reconciliation_service
from worker.core.artifact_manifest import build_artifact_manifest, write_manifest


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _setup_workspace_with_manifest(workspace: Path, execution_id: str = "exec-1") -> Path:
    files = {"app.py": "code", "requirements.txt": "flask"}
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
        workspace_root=workspace, worker_id="w1", artifacts=artifacts,
    )
    manifest_dir = workspace / ".ananta" / "handoff" / execution_id
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "artifact_manifest.v1.json"
    write_manifest(manifest, manifest_path)
    return manifest_path


class TestArtifactReconciliationService:
    def test_dry_run_shows_would_apply(self, workspace: Path) -> None:
        _setup_workspace_with_manifest(workspace)
        svc = get_artifact_reconciliation_service()
        result = svc.dry_run(
            task_id="t1", goal_id="g1", execution_id="exec-1", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path=".ananta/handoff/exec-1/artifact_manifest.v1.json",
        )
        assert result["dry_run"] is True
        assert result["task_id"] == "t1"
        assert "would_apply_decision" in result
        assert "would_apply_status" in result

    def test_apply_requires_actor(self, workspace: Path) -> None:
        _setup_workspace_with_manifest(workspace)
        svc = get_artifact_reconciliation_service()
        with pytest.raises(ValueError, match="actor"):
            svc.apply(
                task_id="t1", goal_id="g1", execution_id="exec-1", trace_id="tr-1",
                workspace_root=workspace,
                manifest_relative_path=".ananta/handoff/exec-1/artifact_manifest.v1.json",
                actor="",
                reason="valid reason",
            )

    def test_apply_requires_reason(self, workspace: Path) -> None:
        _setup_workspace_with_manifest(workspace)
        svc = get_artifact_reconciliation_service()
        with pytest.raises(ValueError, match="reason"):
            svc.apply(
                task_id="t1", goal_id="g1", execution_id="exec-1", trace_id="tr-1",
                workspace_root=workspace,
                manifest_relative_path=".ananta/handoff/exec-1/artifact_manifest.v1.json",
                actor="admin",
                reason="",
            )

    def test_apply_rejects_invalid_path(self, workspace: Path) -> None:
        svc = get_artifact_reconciliation_service()
        result = svc.apply(
            task_id="t1", goal_id="g1", execution_id="exec-x", trace_id="tr-1",
            workspace_root=workspace,
            manifest_relative_path="../outside/manifest.json",
            actor="admin",
            reason="Test invalid path",
        )
        assert result["applied"] is False
        assert "escapes_workspace" in result.get("error", "")
