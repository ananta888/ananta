"""Atomic input placement must never overwrite or follow Workspace targets."""

import os
from types import SimpleNamespace

import pytest

from worker.runtime.native_graph.artifact_workspace import (
    NativeInputWorkspaceMaterializer,
    install_native_workspace_bytes,
)


def test_workspace_install_is_exact_and_idempotent(tmp_path):
    install_native_workspace_bytes(tmp_path, "inputs/report.txt", b"synthetic input")
    install_native_workspace_bytes(tmp_path, "inputs/report.txt", b"synthetic input")
    assert (tmp_path / "inputs/report.txt").read_bytes() == b"synthetic input"
    assert list((tmp_path / "inputs").iterdir()) == [tmp_path / "inputs/report.txt"]


@pytest.mark.parametrize("kind", ["existing", "file-symlink", "directory-symlink", "fifo", "hardlink"])
def test_workspace_target_cannot_overwrite_or_escape_root(tmp_path, kind):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"preserved")
    target = root / "report.txt"
    if kind == "existing":
        target.write_bytes(b"preserved")
    elif kind == "file-symlink":
        target.symlink_to(outside)
    elif kind == "directory-symlink":
        (root / "inputs").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.hardlink_to(outside)
    with pytest.raises(ValueError):
        install_native_workspace_bytes(
            root, "inputs/outside.txt" if kind == "directory-symlink" else "report.txt", b"new",
        )
    assert outside.read_bytes() == b"preserved"
    assert not list(root.glob(".native-input-*"))


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "input//report", "input/./report", "input\\report"])
def test_invalid_paths_fail_before_download(tmp_path, path):
    calls = []
    materializer = NativeInputWorkspaceMaterializer(
        downloader=SimpleNamespace(download=lambda **values: calls.append(True)),
    )
    command = SimpleNamespace(node=SimpleNamespace(
        input_artifacts=("report",), metadata={"native_input_files": {"report": path}},
    ))
    with pytest.raises(ValueError, match="workspace_path_invalid"):
        materializer.materialize(command=command, task={"id": "synthetic-task"}, workspace_dir=tmp_path)
    assert calls == []


def test_failed_second_download_places_no_partial_inputs(tmp_path):
    def download(**values):
        if values["input_name"] == "second":
            raise ValueError("synthetic_download_denied")
        return SimpleNamespace(content=b"first")

    command = SimpleNamespace(node=SimpleNamespace(input_artifacts=("first", "second"), metadata={
        "native_input_files": {"first": "first.txt", "second": "second.txt"},
    }))
    with pytest.raises(ValueError, match="download_denied"):
        NativeInputWorkspaceMaterializer(downloader=SimpleNamespace(download=download)).materialize(
            command=command, task={"id": "task"}, workspace_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
