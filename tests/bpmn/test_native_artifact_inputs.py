"""Current consumer assignment may read only its completed Hub-bound producer."""

import base64
import hashlib
import shlex
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, create_engine, select

from agent.db_models import TaskDB
from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB, WorkflowWorkerAssignmentDB
from agent.repositories.sqlalchemy_support import stable_row_id
from agent.services.artifact_store import ArtifactStore
from agent.services.native_artifact_dispatch import NativeArtifactDispatch
from agent.services.native_artifact_inputs import NativeArtifactInputService
from agent.services.native_result_authority import SQLAlchemyNativeResultAuthority
from agent.services.workflow_runtime.execution_plan import ExecutionNode
from agent.services.workflow_runtime.native_graph_contracts import NativeNodeCommand
from agent.services.workflow_runtime.security import RuntimeAuthorizationEnvelope
from tests.bpmn.test_native_artifact_admission import ingress, materialize, set_result_artifact  # noqa: F401
from tests.bpmn.test_native_artifact_http import HEADERS, FlaskArtifactTransport, http  # noqa: F401
from tests.bpmn.test_native_result_admission import native  # noqa: F401
from tests.pi.test_pi_result_completion_policy import admission, complete  # noqa: F401
from worker.runtime.native_graph.artifact_inputs import NativeArtifactInputDownloader
from worker.runtime.native_graph.artifact_workspace import NativeInputWorkspaceMaterializer
from worker.runtime.native_graph.composition import TaskScopedNativeWorkerExecutor


@pytest.fixture
def consumer(ingress):  # noqa: F811
    receipt, = materialize(ingress)
    set_result_artifact(ingress, receipt["artifact_ref"])
    complete(ingress.native)
    source = ingress.native.command
    envelope = RuntimeAuthorizationEnvelope.issue(
        key_ring=ingress.native.keys, tenant_id=source.tenant_id, workflow_id=source.workflow_id,
        run_id=source.run_id, step_id="consumer", plan_hash=source.plan_hash, policy_version=source.policy_version,
        allowed_artifacts=("report",), now=ingress.native.clock.now,
    )
    command = NativeNodeCommand(
        command_id="synthetic-consumer-command", control_task_id=source.control_task_id, tenant_id=source.tenant_id,
        workflow_id=source.workflow_id, run_id=source.run_id, plan_hash=source.plan_hash,
        policy_version=source.policy_version,
        node=ExecutionNode("consumer", task_kind="tool_task", input_artifacts=("report",)),
        authorization=envelope, attempt_id="synthetic-consumer-attempt", fencing_token=1,
        artifact_refs={"report": receipt["artifact_ref"]},
    )
    ingress.native.grants.grant(envelope)
    task_id = "synthetic-consumer-task"
    with Session(ingress.native.engine) as session:
        ownership = session.exec(select(WorkflowExecutionOwnershipDB)).one().model_dump()
        assignment = session.exec(select(WorkflowWorkerAssignmentDB)).one().model_dump()
        ownership.update(id=stable_row_id("wfro", source.tenant_id, source.run_id, "consumer"),
                         step_id="consumer", attempt_id=command.attempt_id,
                         owner_id=f"hub-native:{source.run_id}:consumer")
        ownership["ownership"] = {**ownership["ownership"], "step_id": "consumer", "attempt_id": command.attempt_id,
                                  "owner_id": ownership["owner_id"]}
        assignment.update(id=stable_row_id("wfra", source.tenant_id, source.run_id, "consumer"),
                          step_id="consumer", attempt_id=command.attempt_id, hub_task_id=task_id)
        session.add(WorkflowExecutionOwnershipDB(**ownership))
        session.add(WorkflowWorkerAssignmentDB(**assignment))
        session.add(TaskDB(
            id=task_id, tenant_id=source.tenant_id, status="in_progress", task_kind="tool_task",
            derivation_reason="native_graph_hub_delegation", assigned_agent_url=assignment["worker_url"],
            worker_execution_context={"schema": "ananta.native_graph_worker_context.v1",
                                      "runtime_path": "native_graph_node", "native_node_command": command.to_dict()},
        ))
        session.commit()
    authority = SQLAlchemyNativeResultAuthority(key_ring=lambda: ingress.native.keys)
    service = NativeArtifactInputService(
        authority=authority, dispatch=NativeArtifactDispatch(authority=authority, worker_id="worker-1",
                                                            worker_url="http://worker-1:5000"),
        storage=ingress.store, clock=lambda: ingress.native.clock.now,
    )
    return SimpleNamespace(ingress=ingress, task_id=task_id, command=command, service=service)


def read(consumer, name="report"):
    with Session(consumer.ingress.native.engine) as session:
        return consumer.service.read(session=session, task_id=consumer.task_id, input_name=name)


def test_current_consumer_reads_exact_hub_bytes(consumer):
    response = read(consumer)
    assert base64.b64decode(response["content_base64"], validate=True) == consumer.ingress.content
    assert response["hub_task_id"] == consumer.task_id


def test_undeclared_input_cannot_be_used_as_generic_artifact_read(consumer):
    with pytest.raises(ValueError, match="input_not_authorized"):
        read(consumer, "foreign")


def test_expired_consumer_lease_denies_byte_access(consumer):
    consumer.ingress.native.clock.now += 1000
    with pytest.raises(ValueError, match="lease_not_current"):
        read(consumer)


@pytest.mark.parametrize("mode", ["accepted-producer", "expired-consumer", "mutated-producer"])
def test_fresh_hub_composition_recovers_only_immutable_accepted_input(consumer, mode):
    """Reopen persisted SQL/storage; no surviving service instance supplies proof."""
    hub = consumer.ingress.native
    with Session(hub.engine) as session:
        source_lease = session.exec(select(WorkflowExecutionOwnershipDB).where(
            WorkflowExecutionOwnershipDB.step_id == hub.command.node.node_id,
        )).one()
        source_lease.lease_expires_at = 1
        session.add(source_lease)
        if mode == "mutated-producer":
            producer = session.get(TaskDB, hub.task_id)
            verification = {**producer.verification_status}
            receipt = {**verification["native_result_receipt"], "classification": "production"}
            producer.verification_status = {**verification, "native_result_receipt": receipt}
            session.add(producer)
        session.commit()
    if mode == "expired-consumer":
        hub.clock.now += 1000
    engine = create_engine(str(hub.engine.url))
    authority = SQLAlchemyNativeResultAuthority(key_ring=lambda: hub.keys)
    restored = NativeArtifactInputService(
        authority=authority,
        dispatch=NativeArtifactDispatch(authority=authority, worker_id="worker-1", worker_url="http://worker-1:5000"),
        storage=ArtifactStore(consumer.ingress.store.base_dir), clock=lambda: hub.clock.now,
    )
    try:
        with Session(engine) as session:
            if mode == "accepted-producer":
                response = restored.read(session=session, task_id=consumer.task_id, input_name="report")
                assert base64.b64decode(response["content_base64"], validate=True) == consumer.ingress.content
            else:
                with pytest.raises(ValueError):
                    restored.read(session=session, task_id=consumer.task_id, input_name="report")
    finally:
        engine.dispose()


def test_completed_producer_requires_its_immutable_result_receipt(consumer):
    with Session(consumer.ingress.native.engine) as session:
        task = session.get(TaskDB, consumer.ingress.native.task_id)
        task.verification_status = {key: value for key, value in task.verification_status.items()
                                    if key != "native_result_receipt"}
        session.add(task)
        session.commit()
    with pytest.raises(ValueError, match="receipt_required"):
        read(consumer)


def test_modified_stored_bytes_never_reach_consumer(consumer):
    from pathlib import Path

    from agent.db_models import ArtifactVersionDB

    with Session(consumer.ingress.native.engine) as session:
        path = session.exec(select(ArtifactVersionDB)).one().storage_path
    Path(path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity_failed"):
        read(consumer)


def test_foreign_authenticated_worker_cannot_read_inputs(consumer):
    consumer.service._dispatch = NativeArtifactDispatch(
        authority=consumer.service._authority, worker_id="foreign", worker_url="http://worker-1:5000",
    )
    with pytest.raises(ValueError, match="worker_assignment_mismatch"):
        read(consumer)


def test_modified_foreign_run_projection_cannot_supply_input(consumer):
    with Session(consumer.ingress.native.engine) as session:
        task = session.get(TaskDB, consumer.ingress.native.task_id)
        context = {**task.worker_execution_context}
        context["native_node_command"] = {**context["native_node_command"], "run_id": "foreign-run"}
        task.worker_execution_context = context
        session.add(task)
        session.commit()
    with pytest.raises(ValueError, match="task_binding_invalid"):
        read(consumer)


def test_producer_from_foreign_project_cannot_supply_input(consumer):
    with Session(consumer.ingress.native.engine) as session:
        task = session.get(TaskDB, consumer.ingress.native.task_id)
        task.project_id = "foreign-project"
        session.add(task)
        session.commit()
    with pytest.raises(ValueError, match="producer_scope_mismatch"):
        read(consumer)


def test_fifo_cannot_block_hub_input_read(consumer):
    import os
    from pathlib import Path

    from agent.db_models import ArtifactVersionDB

    with Session(consumer.ingress.native.engine) as session:
        path = Path(session.exec(select(ArtifactVersionDB)).one().storage_path)
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError, match="integrity_failed"):
        read(consumer)


def test_authenticated_download_checks_hub_proof_and_actual_bytes(consumer, http):  # noqa: F811
    item = NativeArtifactInputDownloader(transport=FlaskArtifactTransport(http)).download(
        command=consumer.command, hub_task_id=consumer.task_id, project_id=None, input_name="report",
    )
    assert item.content == consumer.ingress.content
    assert item.output_name == "report"


@pytest.mark.parametrize("headers", [{}, {**HEADERS, "Authorization": "Bearer invalid"},
                                     {**HEADERS, "X-Ananta-Worker-ID": "foreign"}])
def test_input_http_auth_cannot_be_bypassed(consumer, http, headers):  # noqa: F811
    url = f"/internal/tasks/{consumer.task_id}/native-artifact-inputs/report"
    response = http.client.get(url, headers=headers)
    assert response.status_code in {401, 403}
    assert "content_base64" not in str(response.get_json())


@pytest.mark.parametrize("field,value", [
    ("artifact_ref", "artifact://foreign"), ("sha256", "a" * 64), ("size_bytes", True),
    ("filename", "foreign.txt"), ("consumer_command_digest", "a" * 64),
    ("artifact_version_id", "SRC_forged"), ("content_base64", ""),
])
def test_worker_rejects_modified_input_download(consumer, http, field, value):  # noqa: F811
    def tamper(method, response):
        response[field] = value

    with pytest.raises(ValueError):
        NativeArtifactInputDownloader(transport=FlaskArtifactTransport(http, tamper=tamper)).download(
            command=consumer.command, hub_task_id=consumer.task_id, project_id=None, input_name="report",
        )


def test_input_http_denies_undeclared_artifact_name(consumer, http):  # noqa: F811
    url = f"/internal/tasks/{consumer.task_id}/native-artifact-inputs/foreign"
    response = http.client.get(url, headers=HEADERS)
    assert response.status_code == 409
    assert "content_base64" not in str(response.get_json())


@pytest.mark.parametrize("real_policy", [False, True], ids=["synthetic-command", "actual-policy-command"])
def test_current_consumer_downloads_then_command_reads_workspace_input(consumer, http, tmp_path, real_policy):  # noqa: F811
    from worker.runtime.native_worker_runtime_service import NativeWorkerRuntimeService

    command = replace(consumer.command, node=replace(consumer.command.node, metadata={
        "native_input_files": {"report": "inputs/report.txt"},
    }))
    with Session(consumer.ingress.native.engine) as session:
        task = session.get(TaskDB, consumer.task_id)
        task.worker_execution_context = {**task.worker_execution_context, "native_node_command": command.to_dict()}
        session.add(task)
        session.commit()
        session.refresh(task)
        snapshot = task.model_dump()
    workspace = tmp_path / "consumer-workspace"
    workspace.mkdir()
    calls = []

    def execute(**values):
        calls.append("executed_after_download")
        assert (workspace / "inputs/report.txt").read_bytes() == consumer.ingress.content
        return {"status": "completed"}

    executor = TaskScopedNativeWorkerExecutor(
        runtime=NativeWorkerRuntimeService() if real_policy else SimpleNamespace(execute_and_verify_command=execute),
        workspaces=SimpleNamespace(resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=workspace)),
        input_materializer=NativeInputWorkspaceMaterializer(
            downloader=NativeArtifactInputDownloader(transport=FlaskArtifactTransport(http)),
        ),
    )
    digest = hashlib.sha256(consumer.ingress.content).hexdigest()
    script = (
        "assert __import__('hashlib').sha256(__import__('pathlib').Path('inputs/report.txt').read_bytes())"
        f".hexdigest() == {digest!r}"
    )
    text = shlex.join([sys.executable, "-c", script]) if real_policy else "synthetic-read"
    config = {"worker_runtime": {"native_worker_runtime": {"shell_policy": {"allowlist": [sys.executable]}}}}
    result = executor.execute(hub_task_id=consumer.task_id, task=snapshot, command=text, trace_id="trace",
                              timeout_seconds=10, agent_config=config)
    assert result["status"] == "completed", result
    assert calls == ([] if real_policy else ["executed_after_download"])


def test_missing_input_materializer_prevents_command_execution(consumer, tmp_path):
    task = consumer.ingress.native.repository.get_by_id(consumer.task_id).model_dump()
    calls = []
    executor = TaskScopedNativeWorkerExecutor(
        runtime=SimpleNamespace(execute_and_verify_command=lambda **values: calls.append(True)),
        workspaces=SimpleNamespace(resolve_workspace_context=lambda **values: SimpleNamespace(workspace_dir=tmp_path)),
    )
    with pytest.raises(ValueError, match="input_materializer_unavailable"):
        executor.execute(hub_task_id=consumer.task_id, task=task, command="synthetic", trace_id="trace",
                         timeout_seconds=1, agent_config={})
    assert calls == []
