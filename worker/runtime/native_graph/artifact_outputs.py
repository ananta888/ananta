"""Materialize only explicit output-file declarations of a Hub Native command."""

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from ananta_contracts.native_artifact_files import validate_native_artifact_files
from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_COUNT, MAX_TOTAL_BYTES
from ananta_contracts.native_context_bundle import native_context_digest
from worker.runtime.native_graph.artifact_reader import read_confined_native_bytes
from worker.runtime.native_graph.contracts import NativeNodeCommand


class NativeOutputMaterializationError(ValueError):
    """Execution happened, but its declared outputs cannot be materialized."""


class NativeOutputFileMaterializer:
    def __init__(self, *, writer):
        self._writer = writer

    def prepare(self, task):
        raw = (task.get("worker_execution_context") or {}).get("native_node_command")
        command = NativeNodeCommand.from_mapping(raw)
        if (
            native_context_digest(raw) != native_context_digest(command.to_dict())
            or task.get("tenant_id") != command.tenant_id
        ):
            raise ValueError("native_output_task_binding_invalid")
        if not command.node.output_artifacts:
            return command, {}
        files = command.node.metadata.get("native_output_files")
        if (
            not isinstance(files, dict) or set(files) != set(command.node.output_artifacts)
            or len(files) > MAX_ARTIFACT_COUNT
        ):
            raise ValueError("native_output_files_required")
        try:
            validate_native_artifact_files(
                command.node.metadata, inputs=command.node.input_artifacts, outputs=command.node.output_artifacts,
            )
        except ValueError as exc:
            if str(exc) == "native_artifact_workspace_path_invalid":
                raise ValueError("native_output_file_path_invalid") from exc
            raise
        return command, files

    def materialize(self, *, command, files, task, workspace_dir):
        try:
            candidates = []
            total = 0
            for name, descriptor in files.items():
                path = Path(workspace_dir) / descriptor["path"]
                content = read_confined_native_bytes(workspace_dir, path)
                total += len(content)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("native_output_total_size_exceeded")
                binding = {
                    "hub_task_id": task["id"], "tenant_id": command.tenant_id, "project_id": task.get("project_id"),
                    "command_digest": native_context_digest(command.to_dict()), "attempt_id": command.attempt_id,
                    "fencing_token": command.fencing_token, "output_name": name,
                }
                candidates.append((name, path.name, descriptor["media_type"], content, binding))
            # Snapshot all files before producing local SQL/storage side effects.
            return {name: self._writer.store(filename=filename, media_type=media, content=content, binding=binding)
                    for name, filename, media, content, binding in candidates}
        except (OSError, ValueError, SQLAlchemyError) as exc:
            raise NativeOutputMaterializationError("native_output_materialization_failed") from exc
