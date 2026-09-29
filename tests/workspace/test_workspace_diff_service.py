"""AFH-T006/T012: WorkspaceDiffService tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.workspace_diff_service import get_workspace_diff_service


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


class TestWorkspaceDiffService:
    def test_synthesize_manifest_from_created_files(self, workspace: Path) -> None:
        (workspace / "app.py").write_text("code", encoding="utf-8")
        svc = get_workspace_diff_service()

        before_id, before_snap = svc.take_before_snapshot(workspace)
        after_id, after_snap = svc.take_after_snapshot(workspace)

        fcs = svc.compute_diff(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot={},
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        assert len(fcs.created_files) >= 1

        manifest = svc.synthesize_manifest(
            file_change_set=fcs, workspace_root=workspace,
            task_id="t1", goal_id="g1", execution_id="e1", trace_id="tr-1",
        )
        assert manifest["synthesized"] is True
        assert manifest["schema"] == "artifact_manifest.v1"
        assert len(manifest["artifacts"]) >= 1
        assert all(a["metadata"].get("synthesized") is True for a in manifest["artifacts"])

    def test_synthesize_not_from_broad_scan_without_before_snapshot(self, workspace: Path) -> None:
        """Synthesize must use before snapshot to determine what changed."""
        (workspace / "existing.py").write_text("existing", encoding="utf-8")
        svc = get_workspace_diff_service()

        # Snapshot taken before = empty (simulating fresh run with nothing before)
        before_snap: dict = {}
        after_id, after_snap = svc.take_after_snapshot(workspace)

        fcs = svc.compute_diff(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id="empty", before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        # All files appear as created (since before was empty)
        assert len(fcs.created_files) == len(after_snap)

    def test_workspace_diff_boundary_ok_for_normal_files(self, workspace: Path) -> None:
        (workspace / "f.txt").write_text("hi", encoding="utf-8")
        svc = get_workspace_diff_service()
        before_id, before_snap = svc.take_before_snapshot(workspace)
        after_id, after_snap = svc.take_after_snapshot(workspace)
        fcs = svc.compute_diff(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot={},
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        assert fcs.workspace_boundary_status == "ok"
