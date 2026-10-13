"""Confined no-overwrite workspace placement for authenticated Native inputs."""

import os
import stat
import uuid
from pathlib import Path

from ananta_contracts.native_artifact_files import native_workspace_path, validate_native_artifact_files
from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_BYTES, MAX_ARTIFACT_COUNT, MAX_TOTAL_BYTES


def validate_native_workspace_path(value):
    return native_workspace_path(value)


class NativeInputWorkspaceMaterializer:
    def __init__(self, *, downloader):
        self._downloader = downloader

    def materialize(self, *, command, task, workspace_dir):
        if not command.node.input_artifacts:
            return
        paths = command.node.metadata.get("native_input_files")
        if (
            not isinstance(paths, dict) or set(paths) != set(command.node.input_artifacts)
            or len(paths) > MAX_ARTIFACT_COUNT
        ):
            raise ValueError("native_artifact_input_files_required")
        validate_native_artifact_files(
            command.node.metadata, inputs=command.node.input_artifacts,
            outputs=getattr(command.node, "output_artifacts", ()),
        )
        candidates, total = [], 0
        for name, path in paths.items():
            item = self._downloader.download(
                command=command, hub_task_id=task["id"], project_id=task.get("project_id"), input_name=name,
            )
            total += len(item.content)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("native_artifact_input_total_size_exceeded")
            candidates.append((path, item.content))
        # Validate every download before placing the first input file.
        for path, content in candidates:
            install_native_workspace_bytes(workspace_dir, path, content)


def install_native_workspace_bytes(root, relative_path, content):
    if type(content) is not bytes or len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("native_artifact_workspace_size_invalid")
    parts = validate_native_workspace_path(relative_path).split("/")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = None
    temporary = None
    try:
        directory = os.open(Path(root).absolute(), flags | os.O_DIRECTORY)
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            next_directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        temporary = f".native-input-{uuid.uuid4().hex}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o400, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            descriptor = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != len(content):
                    raise ValueError("native_artifact_workspace_conflict")
                if handle.read(len(content) + 1) != content:
                    raise ValueError("native_artifact_workspace_conflict")
        os.unlink(temporary, dir_fd=directory)
        temporary = None
        os.fsync(directory)
    except OSError as exc:
        raise ValueError("native_artifact_workspace_denied") from exc
    finally:
        if directory is not None:
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)
