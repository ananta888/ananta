from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.workflow_runtime import HmacKeyRing, RuntimeAuthorizationEnvelope
from agent.services.workflow_runtime.execution_plan import ExecutionNode
from agent.services.workflow_runtime.ports import DelegatedExecutionRequest
from ananta_contracts.provider_execution import ProviderExecutionBinding
from worker.runtime.native_graph import NativeNodeCommand, NativeNodeResult
from worker.runtime.native_graph.composition import (
    NativeTaskScopedNodeHandler,
    TaskScopedNativeWorkerExecutor,
)
from worker.runtime.native_graph.execution_adapter import NativeExecutionRuntimeAdapter
from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter


class FakeQueue:
    def __init__(self, repository):
        self.repository = repository
        self.ingested_values = []

    def ingest_task(self, **values):
        self.ingested_values.append(values)
        extra = dict(values["extra_fields"])
        self.repository.values[values["task_id"]] = SimpleNamespace(
            id=values["task_id"],
            status=values["status"],
            verification_status={},
            **extra,
        )


class FakeRepository:
    def __init__(self):
        self.values = {}

    def get_by_id(self, task_id):
        return self.values.get(task_id)


class FakeTaskRuntime:
    def __init__(self, repository):
        self.repository = repository

    def update_local_task_status(self, task_id, status, **values):
        self.repository.values[task_id].status = status


def command() -> NativeNodeCommand:
    keys = HmacKeyRing({"key": "x" * 32}, active_key_id="key")
    authorization = RuntimeAuthorizationEnvelope.issue(
        key_ring=keys,
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id="step-a",
        plan_hash="f" * 64,
        policy_version="policy-v1",
        ttl_seconds=60,
        now=100,
    )
    return NativeNodeCommand(
        command_id="command-a",
        control_task_id="control-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        plan_hash="f" * 64,
        policy_version="policy-v1",
        node=ExecutionNode(node_id="step-a"),
        authorization=authorization,
        attempt_id="attempt-a",
        fencing_token=1,
    )


def result(value: NativeNodeCommand, task_id: str) -> NativeNodeResult:
    return NativeNodeResult(
        result_id="result-a",
        command_id=value.command_id,
        hub_task_id=task_id,
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        node_id=value.node.node_id,
        attempt_id=value.attempt_id,
        fencing_token=value.fencing_token,
        status="completed",
    )


def test_hub_queue_adapter_creates_real_task_polls_contract_and_cancels() -> None:
    repository = FakeRepository()
    queue = FakeQueue(repository)
    adapter = AnantaHubTaskQueueAdapter(
        task_queue=queue,
        task_repository=repository,
        task_runtime=FakeTaskRuntime(repository),
    )
    value = command()

    receipt = adapter.submit(value)
    duplicate = adapter.submit(value)

    assert receipt.accepted and duplicate.accepted
    task = repository.values[receipt.hub_task_id]
    assert task.worker_execution_context["runtime_path"] == "native_graph_node"
    assert task.worker_execution_context["native_node_command"]["control_task_id"] == "control-a"
    # Runtime identities are carried by the typed worker contract.  They are
    # not persisted as relational Task/Plan identities unless those rows
    # actually exist in the Hub database.
    assert {
        "parent_task_id",
        "source_task_id",
        "plan_id",
        "plan_node_id",
    }.isdisjoint(queue.ingested_values[0]["extra_fields"])
    task.status = "completed"
    task.verification_status = {"native_node_result": result(value, receipt.hub_task_id).to_dict()}
    assert adapter.poll(
        tenant_id="tenant-a", run_id="run-a", hub_task_ids=(receipt.hub_task_id,)
    )[0].status == "completed"

    second = command()
    second = NativeNodeCommand(**{**second.__dict__, "command_id": "command-b"})
    pending = adapter.submit(second)
    adapter.cancel(
        tenant_id="tenant-a",
        run_id="run-a",
        hub_task_ids=(pending.hub_task_id,),
        reason="operator_pause",
    )
    assert repository.values[pending.hub_task_id].status == "cancelled"


def test_completed_task_without_canonical_result_is_a_failure_never_synthetic_success() -> None:
    repository = FakeRepository()
    adapter = AnantaHubTaskQueueAdapter(
        task_queue=FakeQueue(repository),
        task_repository=repository,
        task_runtime=FakeTaskRuntime(repository),
    )
    receipt = adapter.submit(command())
    repository.values[receipt.hub_task_id].status = "completed"

    polled = adapter.poll(
        tenant_id="tenant-a", run_id="run-a", hub_task_ids=(receipt.hub_task_id,)
    )

    assert polled[0].status == "failed"
    assert polled[0].reason_code == "native_node_result_contract_missing"


def test_worker_task_adapter_executes_only_the_predelegated_node_contract() -> None:
    value = command()
    expected = result(value, "hub-task-a")

    class Runtime:
        def execute(self, received, *, hub_task_id):
            assert received == value
            assert hub_task_id == "hub-task-a"
            return expected

    adapter = NativeGraphWorkerTaskAdapter(Runtime())
    actual = adapter.execute_task(
        {
            "id": "hub-task-a",
            "worker_execution_context": {
                "schema": "ananta.native_graph_worker_context.v1",
                "runtime_path": "native_graph_node",
                "native_node_command": value.to_dict(),
            },
        }
    )

    assert actual == expected
    assert adapter.verification_update(actual)["native_node_result"]["status"] == "completed"


def test_native_adapter_implements_shared_execution_runtime_port_fail_closed() -> None:
    value = command()

    class Runtime:
        def execute(self, received, *, hub_task_id):
            return result(received, hub_task_id)

    adapter = NativeExecutionRuntimeAdapter(Runtime())
    delegated = DelegatedExecutionRequest(
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        step_id=value.node.node_id,
        attempt_id=value.attempt_id,
        fencing_token=value.fencing_token,
        plan_hash=value.plan_hash,
        policy_version=value.policy_version,
        authorization_envelope=value.authorization.to_dict(),
        parameters={
            "command_id": value.command_id,
            "control_task_id": value.control_task_id,
            "hub_task_id": "hub-task-a",
            "node": value.node.to_dict(),
        },
    )

    completed = adapter.execute(delegated)
    invalid = adapter.execute(
        DelegatedExecutionRequest(**{**delegated.__dict__, "step_id": "other-step"})
    )

    assert completed.status == "completed"
    assert invalid.status == "failed"
    assert "step_binding_mismatch" in invalid.reason_code


def _command_with_declared_output() -> NativeNodeCommand:
    value = command()
    return NativeNodeCommand(
        **{
            **value.__dict__,
            "node": ExecutionNode(
                node_id=value.node.node_id,
                output_artifacts=("declared-output",),
            ),
            "input_data": {"command": "true"},
        }
    )


@pytest.mark.parametrize("runtime_status", ["failed", "degraded"])
def test_native_handler_never_publishes_declared_outputs_on_failure(
    runtime_status: str,
) -> None:
    value = _command_with_declared_output()

    class Executor:
        @staticmethod
        def execute(**_values):
            return {
                "status": runtime_status,
                "failure_type": "runtime_failure",
                "artifact_refs": {
                    "declared-output": (
                        "artifact://materialized/declared-output"
                    )
                },
            }

    handler = NativeTaskScopedNodeHandler(
        agent_config={},
        task_snapshots=SimpleNamespace(
            task_snapshot=lambda **_values: {
                "id": "hub-task-a"
            }
        ),
        executor=Executor(),
    )

    actual = handler.execute(
        value,
        hub_task_id="hub-task-a",
    )

    assert actual.status == "failed"
    assert actual.artifact_refs == {}


def test_native_handler_passes_only_exact_materialized_outputs() -> None:
    value = _command_with_declared_output()
    expected_ref = "artifact://materialized/declared-output"

    class Executor:
        @staticmethod
        def execute(**_values):
            return {
                "status": "completed",
                "artifact_refs": {
                    "declared-output": expected_ref,
                },
            }

    actual = NativeTaskScopedNodeHandler(
        agent_config={},
        task_snapshots=SimpleNamespace(
            task_snapshot=lambda **_values: {
                "id": "hub-task-a"
            }
        ),
        executor=Executor(),
    ).execute(
        value,
        hub_task_id="hub-task-a",
    )

    assert actual.status == "completed"
    assert actual.artifact_refs == {
        "declared-output": expected_ref
    }


def test_native_execution_adapter_drops_failed_result_artifacts() -> None:
    value = _command_with_declared_output()

    class Runtime:
        @staticmethod
        def execute(received, *, hub_task_id):
            return NativeNodeResult(
                result_id="failed-result",
                command_id=received.command_id,
                hub_task_id=hub_task_id,
                tenant_id=received.tenant_id,
                workflow_id=received.workflow_id,
                run_id=received.run_id,
                node_id=received.node.node_id,
                attempt_id=received.attempt_id,
                fencing_token=received.fencing_token,
                status="failed",
                artifact_refs={
                    "declared-output": (
                        "artifact://untrusted/failed-output"
                    )
                },
                reason_code="runtime_failure",
            )

    delegated = DelegatedExecutionRequest(
        tenant_id=value.tenant_id,
        workflow_id=value.workflow_id,
        run_id=value.run_id,
        step_id=value.node.node_id,
        attempt_id=value.attempt_id,
        fencing_token=value.fencing_token,
        plan_hash=value.plan_hash,
        policy_version=value.policy_version,
        authorization_envelope=value.authorization.to_dict(),
        parameters={
            "command_id": value.command_id,
            "control_task_id": value.control_task_id,
            "hub_task_id": "hub-task-a",
            "node": value.node.to_dict(),
            "input_data": value.input_data,
        },
    )

    actual = NativeExecutionRuntimeAdapter(Runtime()).execute(
        delegated
    )

    assert actual.status == "failed"
    assert actual.artifact_refs == ()


def test_provider_needing_native_node_requires_and_round_trips_hub_binding() -> None:
    value = command()
    provider_node = ExecutionNode(
        node_id=value.node.node_id,
        required_capabilities=("text_generation",),
    )
    unbound = NativeNodeCommand(
        **{**value.__dict__, "node": provider_node}
    )

    with pytest.raises(ValueError, match="native_node_provider_binding_required"):
        unbound.assert_valid()

    binding = ProviderExecutionBinding(
        provider_id="lmstudio",
        model_id="model-a",
        source="hub_config.defaults",
        reason_code="hub_provider_policy_selected",
    )
    bound = NativeNodeCommand(
        **{**unbound.__dict__, "provider_binding": binding}
    )

    restored = NativeNodeCommand.from_mapping(bound.to_dict())
    assert restored.provider_binding == binding


def test_native_worker_executor_uses_only_injected_worker_ports() -> None:
    class Workspaces:
        def resolve_workspace_context(self, *, task):
            assert task["id"] == "hub-task-a"
            return SimpleNamespace(workspace_dir="/worker/workspaces/task-a")

    class Runtime:
        def __init__(self):
            self.calls = []

        def execute_and_verify_command(self, **values):
            self.calls.append(values)
            return {"status": "completed"}

    runtime = Runtime()
    executor = TaskScopedNativeWorkerExecutor(
        runtime=runtime,
        workspaces=Workspaces(),
    )

    result_value = executor.execute(
        hub_task_id="hub-task-a",
        task={"id": "hub-task-a"},
        command="pytest -q",
        trace_id="native-graph:command-a",
        timeout_seconds=60,
        agent_config={"default_provider": "lmstudio"},
    )

    assert result_value == {"status": "completed"}
    assert runtime.calls[0]["workspace_dir"] == "/worker/workspaces/task-a"
    assert runtime.calls[0]["tid"] == "hub-task-a"
