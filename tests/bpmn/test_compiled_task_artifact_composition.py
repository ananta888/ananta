"""Compiled BPMN to fenced SQL tasks and real Worker command execution.

Worker dispatch is synthetic and in-process; this is not container evidence.
"""

import shlex
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.db_models.agents import AgentInfoDB
from agent.repositories.tasks import TaskRepository
from agent.services.artifact_store import ArtifactStore
from agent.services.native_artifact_runtime import NativeArtifactRuntime
from agent.services.native_graph_orchestration_service import NativeGraphRequest
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.native_result_completion_policy import NativeResultCompletionPolicy
from agent.services.native_task_ingestion import ingest_native_task_fenced
from agent.services.workflow_runtime import AuthorizationVerifier, InMemoryReplayNonceStore
from agent.services.workflow_worker_assignment_service import (
    SQLAlchemyWorkflowWorkerAssignmentStore,
    WorkflowWorkerAssignment,
)
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from tests.bpmn.completion_helpers import harness as harness  # noqa: F401
from tests.bpmn.completion_helpers import linear_request
from tests.bpmn.test_execution_routing import compile_request, execution_plan
from tests.bpmn.test_native_artifact_file_binding import artifact_document
from tests.test_native_graph_runtime import AllowPolicy
from worker.runtime.native_graph import NativeDelegatedNodeRuntime
from worker.runtime.native_graph.artifact_inputs import NativeArtifactInputDownloader
from worker.runtime.native_graph.artifact_outputs import NativeOutputFileMaterializer
from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher, NativeNodeArtifactPublication
from worker.runtime.native_graph.artifact_reader import LocalNativeArtifactReader
from worker.runtime.native_graph.artifact_repository import LocalArtifactRows
from worker.runtime.native_graph.artifact_workspace import NativeInputWorkspaceMaterializer
from worker.runtime.native_graph.artifact_writer import LocalNativeArtifactWriter
from worker.runtime.native_graph.composition import NativeTaskScopedNodeHandler, TaskScopedNativeWorkerExecutor
from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter
from worker.runtime.native_worker_runtime_service import NativeWorkerRuntimeService
from worker.runtime.workflow_adapter_task_consumer import _native_task_result


class InProcessArtifactTransport:
    """Synthetic authenticated identity; real Hub transaction and byte services."""

    def __init__(self, runtime):
        self.runtime = runtime

    def request(self, *, method, path, payload=None):
        parts = path.split("/")
        identity = {"worker_id": "worker-1", "worker_url": "http://worker-1:5000", "task_id": parts[3]}
        if parts[4] == "native-artifact-inputs" and method == "GET":
            return self.runtime.read_input(**identity, input_name=parts[5])
        assert parts[4] == "native-artifacts" and method in {"GET", "POST"}
        return self.runtime.execute(**identity, transfer=payload)


@pytest.mark.parametrize("artifact_io", [False, True], ids=["plain-tasks", "artifact-handoff"])
def test_compiled_bpmn_fenced_tasks_commit_real_worker_results_across_hub_restart(
    harness, monkeypatch, tmp_path, artifact_io,
):
    monkeypatch.setattr("agent.repositories.tasks._engine", lambda: harness.grant_database)
    repository = TaskRepository(completion_policy=NativeResultCompletionPolicy(
        authority=SQLAlchemyNativeResultAuthority(key_ring=lambda: harness.keys), clock=lambda: harness.now,
    ))

    def ingest(*, lease, **values):
        ingest_native_task_fenced(repository=repository, source_resolver=lambda fields: None,
                                  post_commit=lambda *args, **kwargs: None, lease=lease, **values)

    harness.queue = AnantaHubTaskQueueAdapter(
        task_queue=SimpleNamespace(ingest_task_fenced=ingest), task_repository=repository, task_runtime=None,
    )
    harness.restart()
    with Session(harness.grant_database) as session:
        session.add(AgentInfoDB(
            url="http://worker-1:5000", name="worker-1", role="worker", registration_validated=True,
            registration_provenance=STRICT_WORKER_REGISTRATION_PROVENANCE,
            authorized_capabilities=["workflow.adapter.native"],
        ))
        session.commit()
    assignments = SQLAlchemyWorkflowWorkerAssignmentStore(harness.grant_database)
    config = {"worker_runtime": {"native_worker_runtime": {"shell_policy": {"allowlist": [sys.executable]}}}}
    worker_engine = create_engine(f"sqlite:///{tmp_path / 'worker-artifacts.sqlite'}")
    SQLModel.metadata.create_all(worker_engine)
    worker_storage = ArtifactStore(tmp_path / "worker-artifacts")
    hub_storage = ArtifactStore(tmp_path / "hub-artifacts")
    transport = InProcessArtifactTransport(NativeArtifactRuntime(
        engine=harness.grant_database, key_ring=lambda: harness.keys, storage=hub_storage, clock=lambda: harness.now,
    ))
    rows = LocalArtifactRows(engine=worker_engine, artifact_model=ArtifactDB, version_model=ArtifactVersionDB)

    def workspace_context(*, task):
        directory = tmp_path / "workspaces" / task["id"]
        directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(workspace_dir=directory)

    executor = TaskScopedNativeWorkerExecutor(
        runtime=NativeWorkerRuntimeService(),
        workspaces=SimpleNamespace(resolve_workspace_context=workspace_context),
        output_materializer=NativeOutputFileMaterializer(writer=LocalNativeArtifactWriter(
            engine=worker_engine, artifact_model=ArtifactDB, version_model=ArtifactVersionDB, storage=worker_storage,
        )),
        input_materializer=NativeInputWorkspaceMaterializer(
            downloader=NativeArtifactInputDownloader(transport=transport),
        ),
    )
    handler = NativeTaskScopedNodeHandler(
        agent_config=config,
        task_snapshots=SimpleNamespace(
            task_snapshot=lambda hub_task_id: repository.get_by_id(hub_task_id).model_dump(),
        ),
        executor=executor,
        artifact_publication=NativeNodeArtifactPublication(
            reader=LocalNativeArtifactReader(storage_root=worker_storage.base_dir, load_artifact=rows.load_artifact,
                                            load_version=rows.load_version),
            publisher=NativeArtifactPublisher(transport=transport),
        ),
    )
    worker = NativeGraphWorkerTaskAdapter(NativeDelegatedNodeRuntime(
        handler=handler, authorization_verifier=AuthorizationVerifier(
            harness.keys, InMemoryReplayNonceStore(clock=lambda: harness.now),
        ), policy=AllowPolicy(), capabilities=frozenset({"tool_calling"}), ledger=harness.ledger,
        hub_revalidator=harness.grants, clock=lambda: harness.now,
    ))
    request = replace(linear_request(), input_data={"command": shlex.join([sys.executable, "-c", "print('verified')"])})
    if artifact_io:
        producer = (
            "from pathlib import Path; Path('outputs').mkdir(); "
            "Path('outputs/report.txt').write_bytes(b'handoff')"
        )
        consumer = "assert __import__('pathlib').Path('inputs/report.txt').read_bytes() == b'handoff'"
        request = NativeGraphRequest(
            execution_plan(compile_request(artifact_document())), "synthetic-artifact-run", "synthetic-control",
            input_data={"producer": shlex.join([sys.executable, "-c", producer]),
                        "consumer": shlex.join([sys.executable, "-c", consumer])},
        )
    result = harness.hub.start(request)
    executed = []
    for _ in range(16):
        for task in repository.get_all():
            if task.status != "created":
                continue
            command = task.worker_execution_context["native_node_command"]
            assignments.bind(WorkflowWorkerAssignment(
                tenant_id=task.tenant_id, workflow_id=command["workflow_id"], run_id=command["run_id"],
                step_id=command["node"]["node_id"], attempt_id=command["attempt_id"],
                fencing_token=command["fencing_token"], hub_task_id=task.id, worker_id="worker-1",
                worker_url="http://worker-1:5000", assigned_at=harness.now,
            ))
            task.status, task.assigned_agent_url = "in_progress", "http://worker-1:5000"
            repository.save(task)
            value = worker.execute_task(task.model_dump())
            assert value.status == "completed", value.reason_code
            outer = _native_task_result(value, verification=worker.verification_update(value))
            task.status, task.last_output, task.last_exit_code = "completed", outer.summary, 0
            task.verification_status = {"workflow_adapter_task_result": outer.to_dict(),
                                        "native_node_result": value.to_dict()}
            accepted = repository.save(task)
            assert accepted.verification_status["native_result_receipt"]["classification"] == "technical_observation"
            executed.append(value.node_id)
            harness.restart()
        result = harness.hub.advance(request)
        if result.status in {"completed", "failed", "cancelled"}:
            break
    assert result.status == "completed", result.reason_code
    assert executed == (["producer", "consumer"] if artifact_io else ["work", "after"])
    assert len(repository.get_all()) == 2
    if artifact_io:
        assert result.checkpoint.state.runtime_metadata["artifact_refs"]["report"].startswith(
            "artifact://native-artifact-"
        )
        with Session(harness.grant_database) as session:
            version = session.exec(select(ArtifactVersionDB)).one()
            assert version.size_bytes == len(b"handoff")
    worker_engine.dispose()
