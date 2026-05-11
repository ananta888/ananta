"""AFH-T006: FileChangeSet v1 contract tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from worker.core.file_change_set import diff_snapshots, take_snapshot


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "app.py").write_text("code", encoding="utf-8")
    (ws / "README.md").write_text("docs", encoding="utf-8")
    return ws


class TestFileChangeSet:
    def test_created_files_detected(self, workspace: Path, tmp_path: Path) -> None:
        before_id, before_snap = take_snapshot(workspace)
        # Add a new file
        (workspace / "new_file.txt").write_text("new", encoding="utf-8")
        after_id, after_snap = take_snapshot(workspace)

        fcs = diff_snapshots(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        created_paths = {e.relative_path for e in fcs.created_files}
        assert "new_file.txt" in created_paths

    def test_modified_files_detected(self, workspace: Path) -> None:
        before_id, before_snap = take_snapshot(workspace)
        (workspace / "app.py").write_text("updated code", encoding="utf-8")
        after_id, after_snap = take_snapshot(workspace)

        fcs = diff_snapshots(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        modified_paths = {e.relative_path for e in fcs.modified_files}
        assert "app.py" in modified_paths

    def test_deleted_files_detected(self, workspace: Path) -> None:
        before_id, before_snap = take_snapshot(workspace)
        (workspace / "README.md").unlink()
        after_id, after_snap = take_snapshot(workspace)

        fcs = diff_snapshots(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        deleted_paths = {e.relative_path for e in fcs.deleted_files}
        assert "README.md" in deleted_paths

    def test_no_changes_empty_diff(self, workspace: Path) -> None:
        before_id, before_snap = take_snapshot(workspace)
        after_id, after_snap = take_snapshot(workspace)

        fcs = diff_snapshots(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        assert len(fcs.created_files) == 0
        assert len(fcs.modified_files) == 0
        assert len(fcs.deleted_files) == 0

    def test_fcs_to_dict_schema(self, workspace: Path) -> None:
        before_id, before_snap = take_snapshot(workspace)
        (workspace / "out.txt").write_text("result", encoding="utf-8")
        after_id, after_snap = take_snapshot(workspace)

        fcs = diff_snapshots(
            task_id="t1", execution_id="e1", workspace_root=workspace,
            before_snapshot_id=before_id, before_snapshot=before_snap,
            after_snapshot_id=after_id, after_snapshot=after_snap,
        )
        d = fcs.to_dict()
        assert d["schema"] == "file_change_set.v1"
        assert d["task_id"] == "t1"
        assert "created_files" in d
        assert "modified_files" in d
        assert "deleted_files" in d
