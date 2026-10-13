"""Task-bound output materialization using separate Worker SQL and real files."""

import shlex
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.services.artifact_store import ArtifactStore
from tests.test_native_graph_task_adapters import _command_with_declared_output
from worker.runtime.native_graph.artifact_outputs import NativeOutputFileMaterializer, NativeOutputMaterializationError
from worker.runtime.native_graph.artifact_reader import LocalNativeArtifactReader
from worker.runtime.native_graph.artifact_repository import LocalArtifactRows
from worker.runtime.native_graph.artifact_writer import LocalNativeArtifactWriter
from worker.runtime.native_graph.composition import NativeTaskScopedNodeHandler, TaskScopedNativeWorkerExecutor


@pytest.fixture
def outputs(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    SQLModel.metadata.create_all(engine)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = ArtifactStore(tmp_path / "worker-artifacts")
    rows = LocalArtifactRows(engine=engine, artifact_model=ArtifactDB, version_model=ArtifactVersionDB)
    writer = LocalNativeArtifactWriter(engine=engine, artifact_model=ArtifactDB, version_model=ArtifactVersionDB,
                                       storage=storage)
    materializer = NativeOutputFileMaterializer(writer=writer)
    command = _command_with_declared_output()
    command = replace(command, node=replace(command.node, metadata={**command.node.metadata, "native_output_files": {
        "declared-output": {"path": "result.txt", "media_type": "text/plain"},
    }}))
    task = {"id": "synthetic-task", "tenant_id": command.tenant_id, "project_id": "project",
            "worker_execution_context": {"native_node_command": command.to_dict()}}
    reader = LocalNativeArtifactReader(storage_root=storage.base_dir, load_artifact=rows.load_artifact,
                                      load_version=rows.load_version)
    try:
        yield SimpleNamespace(engine=engine, workspace=workspace, storage=storage, materializer=materializer,
                              command=command, task=task, reader=reader)
    finally:
        engine.dispose()


def materialize(outputs):
    command, files = outputs.materializer.prepare(outputs.task)
    return outputs.materializer.materialize(command=command, files=files, task=outputs.task,
                                            workspace_dir=outputs.workspace)


def test_output_snapshot_is_task_bound_immutable_and_retryable(outputs):
    (outputs.workspace / "result.txt").write_bytes(b"synthetic output\n")
    references = materialize(outputs)
    assert materialize(outputs) == references
    actual = outputs.reader.read(command=outputs.command, task=outputs.task, output_name="declared-output",
                                  reference=references["declared-output"])
    assert actual.content == b"synthetic output\n"
    with Session(outputs.engine) as session:
        assert len(session.exec(select(ArtifactDB)).all()) == 1
        assert len(session.exec(select(ArtifactVersionDB)).all()) == 1


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "a/../result.txt", "a\\result.txt", "a//result.txt"])
def test_invalid_declaration_fails_before_command_execution(outputs, path):
    outputs.task["worker_execution_context"]["native_node_command"]["node"]["metadata"]["native_output_files"][
        "declared-output"
    ]["path"] = path
    calls = []
    executor = TaskScopedNativeWorkerExecutor(
        runtime=SimpleNamespace(execute_and_verify_command=lambda **values: calls.append(True)),
        workspaces=SimpleNamespace(
            resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=outputs.workspace),
        ),
        output_materializer=outputs.materializer,
    )
    with pytest.raises(ValueError, match="native_output_file_path_invalid"):
        executor.execute(hub_task_id=outputs.task["id"], task=outputs.task, command="synthetic", trace_id="trace",
                         timeout_seconds=1, agent_config={})
    assert calls == []


def test_successful_executor_replaces_trace_descriptors_with_stored_outputs(outputs):
    def execute(**values):
        (outputs.workspace / "result.txt").write_bytes(b"actual produced bytes")
        return {"status": "completed", "artifact_refs": [{"trace_bundle_ref": "synthetic-trace"}]}

    executor = TaskScopedNativeWorkerExecutor(
        runtime=SimpleNamespace(execute_and_verify_command=execute),
        workspaces=SimpleNamespace(
            resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=outputs.workspace),
        ),
        output_materializer=outputs.materializer,
    )
    result = executor.execute(hub_task_id=outputs.task["id"], task=outputs.task, command="synthetic", trace_id="trace",
                              timeout_seconds=1, agent_config={})
    assert set(result["artifact_refs"]) == {"declared-output"}
    assert outputs.reader.read(command=outputs.command, task=outputs.task, output_name="declared-output",
                               reference=result["artifact_refs"]["declared-output"]).content == b"actual produced bytes"


@pytest.mark.parametrize("mode", ["missing", "symlink"])
def test_unreadable_output_is_post_execution_uncertainty_not_success(outputs, mode):
    if mode == "symlink":
        outside = outputs.workspace.parent / "outside.txt"
        outside.write_bytes(b"must not transfer")
        (outputs.workspace / "result.txt").symlink_to(outside)
    with pytest.raises(NativeOutputMaterializationError):
        materialize(outputs)
    with Session(outputs.engine) as session:
        assert session.exec(select(ArtifactDB)).all() == []


def test_handler_propagates_post_execution_materialization_failure(outputs):
    calls = []

    def execute(**values):
        calls.append("executed")
        return {"status": "completed"}

    executor = TaskScopedNativeWorkerExecutor(
        runtime=SimpleNamespace(execute_and_verify_command=execute),
        workspaces=SimpleNamespace(
            resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=outputs.workspace),
        ),
        output_materializer=outputs.materializer,
    )
    handler = NativeTaskScopedNodeHandler(
        agent_config={}, task_snapshots=SimpleNamespace(task_snapshot=lambda **values: outputs.task),
        executor=executor, artifact_publication=object(),
    )
    with pytest.raises(NativeOutputMaterializationError):
        handler.execute(outputs.command, hub_task_id=outputs.task["id"])
    assert calls == ["executed"]


def test_missing_output_materializer_denies_before_execution(outputs):
    calls = []
    executor = TaskScopedNativeWorkerExecutor(
        runtime=SimpleNamespace(execute_and_verify_command=lambda **values: calls.append(True)),
        workspaces=SimpleNamespace(
            resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=outputs.workspace),
        ),
    )
    with pytest.raises(ValueError, match="native_artifact_output_materializer_unavailable"):
        executor.execute(hub_task_id=outputs.task["id"], task=outputs.task, command="synthetic", trace_id="trace",
                         timeout_seconds=1, agent_config={})
    assert calls == []


@pytest.mark.parametrize("allowed", [True, False], ids=["actual-command", "approval-denied"])
def test_real_worker_policy_execution_materializes_only_successful_output(outputs, allowed):
    from worker.runtime.native_worker_runtime_service import NativeWorkerRuntimeService

    executor = TaskScopedNativeWorkerExecutor(
        runtime=NativeWorkerRuntimeService(),
        workspaces=SimpleNamespace(
            resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=outputs.workspace),
        ),
        output_materializer=outputs.materializer,
    )
    script = "from pathlib import Path; Path('result.txt').write_bytes(b'policy-checked output')"
    command = shlex.join([sys.executable, "-c", script])
    config = {"worker_runtime": {"native_worker_runtime": {"shell_policy": {
        "allowlist": [sys.executable],
        "approval_required_commands": [] if allowed else [sys.executable],
    }}}}
    result = executor.execute(hub_task_id=outputs.task["id"], task=outputs.task, command=command,
                              trace_id="synthetic-policy-test", timeout_seconds=10, agent_config=config)
    if allowed:
        assert result["status"] == "completed", result
        artifact = outputs.reader.read(command=outputs.command, task=outputs.task, output_name="declared-output",
                                       reference=result["artifact_refs"]["declared-output"])
        assert artifact.content == b"policy-checked output"
    else:
        assert result["status"] == "blocked", result
        assert result["failure_type"] == "approval_required", result
        assert not (outputs.workspace / "result.txt").exists()
        with Session(outputs.engine) as session:
            assert session.exec(select(ArtifactDB)).all() == []
