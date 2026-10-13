"""Worker-local byte admission cannot read outside storage or cross assignments."""

import hashlib
from types import SimpleNamespace

import pytest

from ananta_contracts.native_context_bundle import native_context_digest
from worker.runtime.native_graph.artifact_reader import LocalNativeArtifactReader


@pytest.fixture
def local(tmp_path):
    root = tmp_path / "worker-artifacts"
    path = root / "local-id" / "v0001__result.txt"
    path.parent.mkdir(parents=True)
    content = b"synthetic local result\n"
    path.write_bytes(content)
    command = SimpleNamespace(tenant_id="tenant", attempt_id="attempt", fencing_token=1,
                              to_dict=lambda: {"command_id": "synthetic-command"})
    task = {"id": "task", "project_id": "project"}
    binding = {
        "hub_task_id": "task", "tenant_id": "tenant", "project_id": "project",
        "command_digest": native_context_digest(command.to_dict()), "attempt_id": "attempt",
        "fencing_token": 1, "output_name": "report",
    }
    artifact = SimpleNamespace(id="local-id", status="stored", latest_version_id="local-version",
                               latest_sha256=hashlib.sha256(content).hexdigest(),
                               artifact_metadata={"native_output_binding": binding})
    version = SimpleNamespace(artifact_id="local-id", size_bytes=len(content), sha256=artifact.latest_sha256,
                              storage_path=str(path), original_filename="result.txt", media_type="text/plain")
    reader = LocalNativeArtifactReader(storage_root=root, load_artifact=lambda identity: artifact,
                                      load_version=lambda identity: version)
    return SimpleNamespace(root=root, path=path, content=content, command=command, task=task,
                           artifact=artifact, version=version, reader=reader)


def read(local):
    return local.reader.read(command=local.command, task=local.task, output_name="report",
                             reference="artifact://local-id")


def test_exact_local_output_bytes(local):
    assert read(local).content == local.content


@pytest.mark.parametrize("field,value", [
    ("hub_task_id", "foreign"), ("tenant_id", "foreign"), ("project_id", "foreign"),
    ("attempt_id", "foreign"), ("fencing_token", True), ("output_name", "other"),
    ("command_digest", "a" * 64),
])
def test_cross_task_or_coerced_binding_is_denied(local, field, value):
    local.artifact.artifact_metadata["native_output_binding"][field] = value
    with pytest.raises(ValueError, match="local_binding_invalid"):
        read(local)


@pytest.mark.parametrize("mode", ["outside", "parent", "file-symlink", "directory-symlink", "hardlink", "tamper"])
def test_hostile_storage_cannot_read_arbitrary_bytes(local, tmp_path, mode):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(local.content)
    if mode == "outside":
        local.version.storage_path = str(outside)
    elif mode == "parent":
        local.version.storage_path = str(local.root / ".." / "outside.txt")
    elif mode == "file-symlink":
        local.path.unlink()
        local.path.symlink_to(outside)
    elif mode == "directory-symlink":
        local.path.unlink()
        local.path.parent.rmdir()
        local.path.parent.symlink_to(tmp_path, target_is_directory=True)
        local.version.storage_path = str(local.path.parent / "outside.txt")
    elif mode == "hardlink":
        local.path.unlink()
        local.path.hardlink_to(outside)
    else:
        local.path.write_bytes(b"tampered bytes")
    with pytest.raises(ValueError):
        read(local)
    assert outside.read_bytes() == local.content


def test_fifo_is_denied_without_waiting_for_writer(local):
    import os

    local.path.unlink()
    os.mkfifo(local.path)
    with pytest.raises(ValueError, match="local_path_denied"):
        read(local)


def test_artifact_uri_cannot_be_a_filesystem_path(local):
    with pytest.raises(ValueError, match="local_reference_invalid"):
        local.reader.read(command=local.command, task=local.task, output_name="report",
                          reference="artifact://../../outside")
