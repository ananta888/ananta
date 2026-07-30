from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.sources.registered_workspace_connector import (
    PosixWorkspaceFileInspection,
    RegisteredWorkspace,
    RegisteredWorkspaceConnector,
    RegisteredWorkspaceError,
    WorkspaceInventoryLimits,
)


class _Catalog:
    def __init__(self, workspace: RegisteredWorkspace) -> None:
        self.workspace = workspace

    def get(self, **scope):
        if (
            scope["workspace_id"] == self.workspace.workspace_id
            and scope["tenant_id"] == self.workspace.tenant_id
            and scope["project_id"] == self.workspace.project_id
        ):
            return self.workspace
        return None


def _connector(
    root: Path,
    *,
    inspection=None,
    limits: WorkspaceInventoryLimits | None = None,
) -> RegisteredWorkspaceConnector:
    workspace = RegisteredWorkspace(
        workspace_id="workspace-example",
        tenant_id="tenant-example",
        project_id="project-example",
        root=root,
        enabled=True,
        read_only=True,
    )
    return RegisteredWorkspaceConnector(
        catalog=_Catalog(workspace),
        limits=limits or WorkspaceInventoryLimits(
            max_files=10,
            max_total_bytes=1024,
            max_file_bytes=512,
            max_depth=4,
        ),
        inspection=inspection,
    )


def test_inventory_is_sorted_bounded_and_revision_stable(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    connector = _connector(tmp_path)

    first = connector.inventory(
        tenant_id="tenant-example",
        project_id="project-example",
        workspace_id="workspace-example",
        relative_path="src",
    )
    second = connector.inventory(
        tenant_id="tenant-example",
        project_id="project-example",
        workspace_id="workspace-example",
        relative_path="src",
    )

    assert [entry.relative_path for entry in first.entries] == ["a.py", "b.py"]
    assert first == second
    delegated = connector.delegated_reference(
        manifest=first,
        assignment_id="assignment-example",
        lease_id="lease-example",
    )
    assert delegated.access_mode == "read_only"
    assert not hasattr(delegated, "host_path")


@pytest.mark.parametrize(
    "relative_path",
    ("../outside", "/absolute", "src\\file", "src/\x00file"),
)
def test_arbitrary_host_paths_are_rejected(
    tmp_path: Path,
    relative_path: str,
) -> None:
    connector = _connector(tmp_path)

    with pytest.raises(RegisteredWorkspaceError):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
            relative_path=relative_path,
        )


def test_symlink_and_hardlink_are_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    connector = _connector(tmp_path)

    with pytest.raises(RegisteredWorkspaceError, match="symlink"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
            relative_path="link",
        )
    (tmp_path / "link").unlink()

    original = tmp_path / "original.txt"
    original.write_text("same inode", encoding="utf-8")
    os.link(original, tmp_path / "alias.txt")
    with pytest.raises(RegisteredWorkspaceError, match="hardlink"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
        )


def test_cross_project_workspace_is_hidden(tmp_path: Path) -> None:
    connector = _connector(tmp_path)

    with pytest.raises(RegisteredWorkspaceError, match="not_found"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="other-project",
            workspace_id="workspace-example",
        )


def test_sparse_file_is_rejected(tmp_path: Path) -> None:
    sparse = tmp_path / "sparse.bin"
    with sparse.open("wb") as handle:
        handle.seek(8191)
        handle.write(b"\0")
    connector = _connector(
        tmp_path,
        limits=WorkspaceInventoryLimits(
            max_files=10,
            max_total_bytes=32_000,
            max_file_bytes=16_000,
            max_depth=4,
        ),
    )

    with pytest.raises(RegisteredWorkspaceError, match="sparse"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
        )


class _MountChangingInspection(PosixWorkspaceFileInspection):
    def __init__(self, target: Path) -> None:
        self._target = target

    def stat(self, path: Path, *, follow_symlinks: bool):
        value = super().stat(path, follow_symlinks=follow_symlinks)
        if path == self._target:
            class Changed:
                st_dev = value.st_dev + 1
                st_mode = value.st_mode

            return Changed()
        return value


def test_target_mount_change_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    connector = _connector(
        tmp_path,
        inspection=_MountChangingInspection(target),
    )

    with pytest.raises(RegisteredWorkspaceError, match="mount_changed"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
            relative_path="src",
        )


class _HashTimeMutationInspection(PosixWorkspaceFileInspection):
    def hash_file(self, path: Path, *, before):
        digest = super().hash_file(path, before=before)
        path.write_text("changed-during-hash", encoding="utf-8")
        return digest


def test_hash_time_path_race_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("original", encoding="utf-8")
    connector = _connector(
        tmp_path,
        inspection=_HashTimeMutationInspection(),
    )

    with pytest.raises(RegisteredWorkspaceError, match="path_race"):
        connector.inventory(
            tenant_id="tenant-example",
            project_id="project-example",
            workspace_id="workspace-example",
        )
