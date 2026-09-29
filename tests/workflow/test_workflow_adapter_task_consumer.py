from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from flask import Flask

from agent.services.workflow_runtime import HmacKeyRing, RuntimeAuthorizationEnvelope
from agent.services.workflow_runtime.execution_plan import ExecutionNode
from ananta_contracts.provider_execution import ProviderExecutionBinding
from ananta_contracts.workflow_adapter_task import (
    WORKFLOW_ADAPTER_RUNTIME_PATH,
    WORKFLOW_ADAPTER_TASK_SCHEMA,
)
from worker.adapters.workflow_adapter_base import DryRunResult, WorkflowArtifactResult
from worker.runtime.native_graph import NativeNodeCommand, NativeNodeResult
from worker.runtime.workflow_adapter_task_consumer import (
    ExecutionAuthorizationDecision,
    WorkflowAdapterTaskConsumer,
)
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
from worker.runtime.workflow_hub_gateway import HubExecutionAuthorizationAdapter


def _authorization(*, step_id: str = "step-a") -> RuntimeAuthorizationEnvelope:
    return RuntimeAuthorizationEnvelope.issue(
        key_ring=HmacKeyRing({"key": "x" * 32}, active_key_id="key"),
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        step_id=step_id,
        plan_hash="f" * 64,
        policy_version="policy-v1",
        ttl_seconds=60,
        now=100,
    )


def _langgraph_task(*, command: str = "execute") -> dict:
    provider_binding = (
        ProviderExecutionBinding(
            provider_id="lmstudio",
            model_id="model-a",
            source="hub_config.defaults",
            reason_code="hub_provider_policy_selected",
        )
        if command == "execute"
        else None
    )
    provider_context = {
        "tenant_id": "tenant-a",
        "workflow_id": "workflow-a",
        "run_id": "run-a",
        "step_id": "step-a",
        "plan_hash": "f" * 64,
        "policy_version": "policy-v1",
        "prompt_version": "prompt-v1",
        "provider_transport_mode": (
            "hub_bound" if provider_binding is not None else "none"
        ),
        "provider_decision_reason": (
            "hub_provider_policy_selected"
            if provider_binding is not None
            else "provider_transport_not_required"
        ),
        "require_hub_provider_budget": provider_binding is not None,
    }
    if provider_binding is not None:
        provider_context.update(
            {
                "provider_binding_id": provider_binding.binding_id,
                "selected_provider_id": provider_binding.provider_id,
                "selected_model_id": provider_binding.model_id,
            }
        )
    return {
        "id": "hub-task-a",
        "worker_execution_context": {
            "schema": WORKFLOW_ADAPTER_TASK_SCHEMA,
            "runtime_path": WORKFLOW_ADAPTER_RUNTIME_PATH,
            "tenant_id": "tenant-a",
            "workflow_id": "workflow-a",
            "run_id": "run-a",
            "step_id": "step-a",
            "plan_hash": "f" * 64,
            "policy_version": "policy-v1",
            "adapter_kind": "langgraph",
            "command": command,
            "task_type": "agent_workflow",
            "attempt_id": "attempt-a",
            "fencing_token": 7,
            "authorization_envelope": _authorization().to_dict(),
            "provider_binding": (
                provider_binding.to_dict() if provider_binding is not None else None
            ),
            "payload": {
                "tenant_id": "malicious-tenant",
                "graph_descriptor": {"nodes": [], "edges": []},
                "provider_context": provider_context,
            },
        },
    }


class Authorization:
    def __init__(self, allowed: bool = True, reason: str = "hub_execution_authorized") -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls = []

    def authorize(self, **values):
        self.calls.append(values)
        return ExecutionAuthorizationDecision(self.allowed, self.reason)


class LangGraph:
    def __init__(self) -> None:
        self.execute_calls = []
        self.dry_run_calls = []

    def execute(self, **values):
        self.execute_calls.append(values)
        return WorkflowArtifactResult(
            adapter_id="adapter.langgraph",
            task_id=values["task_id"],
            task_type=values["task_type"],
            status="success",
            summary="completed",
            artifacts=[{"artifact_id": "artifact-a"}],
            sources=[{"source_id": "SRC_1"}],
        )

    def dry_run(self, **values):
        self.dry_run_calls.append(values)
        return DryRunResult(
            adapter_id="adapter.langgraph",
            task_id=values["task_id"],
            task_type=values["task_type"],
        )


def test_langgraph_consumer_revalidates_hub_binding_and_executes_one_task() -> None:
    authorization = Authorization()
    adapter = LangGraph()
    consumer = WorkflowAdapterTaskConsumer(
        authorization=authorization,
        langgraph_adapter=adapter,
    )

    result = consumer.consume(_langgraph_task())

    assert result.status == "success"
    assert result.verification_update()["workflow_adapter_task_result"]["hub_task_id"] == "hub-task-a"
    assert authorization.calls[0]["binding"].tenant_id == "tenant-a"
    assert authorization.calls[0]["fencing_token"] == 7
    assert adapter.execute_calls[0]["payload"]["tenant_id"] == "tenant-a"
    assert adapter.execute_calls[0]["payload"]["step_id"] == "step-a"


def test_hub_denial_stops_adapter_before_execution() -> None:
    authorization = Authorization(False, "workflow_worker_fencing_mismatch")
    adapter = LangGraph()
    consumer = WorkflowAdapterTaskConsumer(
        authorization=authorization,
        langgraph_adapter=adapter,
    )

    result = consumer.consume(_langgraph_task())

    assert result.status == "blocked"
    assert result.reason_code == "workflow_worker_fencing_mismatch"
    assert adapter.execute_calls == []


def test_langgraph_dry_run_uses_same_versioned_consumer() -> None:
    adapter = LangGraph()
    consumer = WorkflowAdapterTaskConsumer(
        authorization=Authorization(),
        langgraph_adapter=adapter,
    )

    result = consumer.consume(_langgraph_task(command="dry_run"))

    assert result.status == "success"
    assert adapter.dry_run_calls[0]["payload"]["run_id"] == "run-a"
    assert adapter.execute_calls == []


def test_adapter_result_with_embedded_secret_is_rejected_before_hub_callback() -> None:
    class UnsafeLangGraph(LangGraph):
        def execute(self, **values):
            return WorkflowArtifactResult(
                adapter_id="adapter.langgraph",
                task_id=values["task_id"],
                task_type=values["task_type"],
                status="success",
                summary="unsafe",
                artifacts=[{"artifact_id": "artifact-a", "password": "leaked"}],
            )

    consumer = WorkflowAdapterTaskConsumer(
        authorization=Authorization(),
        langgraph_adapter=UnsafeLangGraph(),
    )

    result = consumer.consume(_langgraph_task())

    assert result.status == "failed"
    assert result.reason_code == "workflow_adapter_result_unsafe"
    assert result.artifacts == ()


def test_native_consumer_executes_existing_native_task_contract() -> None:
    command = NativeNodeCommand(
        command_id="command-a",
        control_task_id="control-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        plan_hash="f" * 64,
        policy_version="policy-v1",
        node=ExecutionNode(node_id="step-a"),
        authorization=_authorization(),
        attempt_id="attempt-a",
        fencing_token=3,
    )
    expected = NativeNodeResult(
        result_id="result-a",
        command_id="command-a",
        hub_task_id="hub-native-a",
        tenant_id="tenant-a",
        workflow_id="workflow-a",
        run_id="run-a",
        node_id="step-a",
        attempt_id="attempt-a",
        fencing_token=3,
        status="completed",
        artifact_refs={"report": "artifact://report-a"},
    )

    class NativeAdapter:
        def __init__(self) -> None:
            self.calls = []

        def execute_task(self, task):
            self.calls.append(task)
            return expected

        def verification_update(self, result):
            return {"schema": "ananta.native_graph_task_verification.v1", "native_node_result": result.to_dict()}

    native = NativeAdapter()
    authorization = Authorization()
    consumer = WorkflowAdapterTaskConsumer(
        authorization=authorization,
        native_adapter=native,
    )
    task = {
        "id": "hub-native-a",
        "worker_execution_context": {
            "schema": "ananta.native_graph_worker_context.v1",
            "runtime_path": "native_graph_node",
            "native_node_command": command.to_dict(),
        },
    }

    result = consumer.consume(task)

    assert result.status == "success"
    assert result.artifacts[0]["reference"] == "artifact://report-a"
    assert authorization.calls[0]["adapter_kind"] == "native"
    assert native.calls == [task]


def test_unknown_or_stale_context_never_falls_through_to_an_adapter() -> None:
    adapter = LangGraph()
    consumer = WorkflowAdapterTaskConsumer(
        authorization=Authorization(),
        langgraph_adapter=adapter,
    )
    unknown = {
        "id": "hub-task-a",
        "worker_execution_context": {"schema": "unknown.v1", "runtime_path": "workflow_adapter"},
    }
    stale = _langgraph_task()
    stale_context = dict(stale["worker_execution_context"])
    stale_context["authorization_envelope"] = replace(
        _authorization(), tenant_id="tenant-b"
    ).to_dict()
    stale["worker_execution_context"] = stale_context

    unsupported = consumer.consume(unknown)
    malformed = consumer.consume(stale)

    assert consumer.supports(unknown) is False
    assert unsupported.status == "unsupported"
    assert malformed.status == "failed"
    assert malformed.reason_code == "authorization_binding_mismatch"
    assert adapter.execute_calls == []


def test_consumer_surface_has_no_queue_or_worker_delegation_capability() -> None:
    public = {
        name
        for name in dir(WorkflowAdapterTaskConsumer)
        if not name.startswith("_")
    }
    assert public == {"consume", "supports"}


def test_http_hub_execution_authorization_adapter_uses_versioned_command() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def command(self, name, **values):
            self.calls.append((name, values))
            return {
                "schema": "ananta.workflow-runtime-worker-decision.v1",
                "allowed": True,
                "reason_code": "hub_execution_authorized",
            }

    client = Client()
    adapter = HubExecutionAuthorizationAdapter(client)
    task_context = _langgraph_task()["worker_execution_context"]
    from ananta_contracts.workflow_adapter_task import WorkflowAdapterTask

    contract = WorkflowAdapterTask.from_mapping(task_context)

    decision = adapter.authorize(
        binding=contract.worker_binding(),
        adapter_kind="langgraph",
        attempt_id="attempt-a",
        fencing_token=7,
    )

    assert decision.allowed is True
    assert client.calls[0][0] == "authorize_execution"
    assert client.calls[0][1]["binding"]["run_id"] == "run-a"


def test_worker_execution_composition_returns_canonical_verification() -> None:
    consumer = WorkflowAdapterTaskConsumer(
        authorization=Authorization(),
        langgraph_adapter=LangGraph(),
    )
    app = Flask(__name__)
    app.extensions["workflow_adapter_task_consumer"] = consumer

    with app.app_context():
        response = consume_delegated_workflow_task(_langgraph_task())

    assert response is not None
    assert response["status"] == "completed"
    assert response["workflow_adapter_verification"]["schema"] == (
        "ananta.workflow-adapter-task-verification.v1"
    )
    assert response["workflow_adapter_verification"]["workflow_adapter_task_result"][
        "adapter_kind"
    ] == "langgraph"


def test_forwarded_result_merges_native_verification_for_hub_polling(monkeypatch) -> None:
    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    native_result = {
        "schema": "ananta.native_node_result.v1",
        "result_id": "result-a",
        "hub_task_id": "hub-native-a",
        "status": "completed",
    }
    response = {
        "status": "completed",
        "workflow_adapter_verification": {
            "schema": "ananta.workflow-adapter-task-verification.v1",
            "workflow_adapter_task_result": {
                "adapter_kind": "native",
                "adapter_result": {
                    "verification": {
                        "schema": "ananta.native_graph_task_verification.v1",
                        "native_node_result": native_result,
                    }
                },
            },
        },
    }

    persist_forwarded_execution(
        tid="hub-native-a",
        response=response,
        task={"history": [], "last_proposal": {}, "verification_status": {}},
        request_data=SimpleNamespace(command=None),
    )

    verification = updates[0][1]["verification_status"]
    assert verification["native_node_result"] == native_result
    assert verification["workflow_adapter_task_result"]["adapter_kind"] == "native"


def test_forwarded_knowledge_index_result_is_validated_and_persisted(monkeypatch) -> None:
    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []
    validated = []

    class JobService:
        def materialize_worker_result(
            self,
            *,
            job_id,
            result,
            task,
            authenticated_worker_id,
            transfer_deadline,
        ):
            assert job_id == "knowledge-index-" + "a" * 32
            assert task["verification_status"] == {}
            assert authenticated_worker_id is None
            assert transfer_deadline is None
            assert set(result) == {
                "schema",
                "job_id",
                "idempotency_fingerprint",
                "status",
                "reason_code",
                "knowledge_index",
                "run",
                "results",
                "artifact_refs",
                "error",
            }
            validated.append(result)
            return dict(result)

    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.get_core_services",
        lambda: SimpleNamespace(knowledge_index_job_service=JobService()),
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    job_id = "knowledge-index-" + "a" * 32
    response = {
        "schema": "ananta.knowledge_index_job_result.v1",
        "job_id": job_id,
        "idempotency_fingerprint": "b" * 64,
        "status": "completed",
        "reason_code": None,
        "knowledge_index": {"id": "idx-1"},
        "run": {"id": "run-1"},
        "results": None,
        "artifact_refs": [],
        "error": None,
        "handler_contract": {"task_kind": "codecompass_index_build"},
    }

    persist_forwarded_execution(
        tid=job_id,
        response=response,
        task={"history": [], "last_proposal": {}, "verification_status": {}},
        request_data=SimpleNamespace(command=None),
    )

    assert len(validated) == 1
    verification = updates[0][1]["verification_status"]
    assert verification["knowledge_index_job_result"]["knowledge_index"]["id"] == "idx-1"


def test_forwarded_visual_process_result_dispatches_to_hub_acceptance(monkeypatch) -> None:
    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []
    accepted_results = []

    class AssistantService:
        def accept_worker_result(self, *, task_id, result):
            assert task_id == "vpa-retrieval-task"
            assert "handler_contract" not in result
            accepted_results.append(result)
            return {
                "request_id": result["request_id"],
                "status": "queued_inference",
                "prompt_context_id": "ctx-prompt",
            }

    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding._get_visual_process_assistant_service",
        lambda: AssistantService(),
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    response = {
        "schema": "ananta.visual_process_assistant.retrieval_result.v1",
        "task_id": "vpa-retrieval-task",
        "request_id": "vpa-request-a",
        "context_id": "ctx-source",
        "status": "completed",
        "evidence": [],
        "rejected_count": 0,
        "rejection_reasons": [],
        "consistency_state": "current",
        "handler_contract": {"task_kind": "visual_process_assistant_retrieval"},
    }

    persist_forwarded_execution(
        tid="vpa-retrieval-task",
        response=response,
        task={
            "task_kind": "visual_process_assistant_retrieval",
            "history": [],
            "last_proposal": {},
            "verification_status": {},
        },
        request_data=SimpleNamespace(command=None),
    )

    assert len(accepted_results) == 1
    verification = updates[0][1]["verification_status"]
    assert verification["visual_process_assistant_request"] == {
        "request_id": "vpa-request-a",
        "status": "queued_inference",
        "prompt_context_id": "ctx-prompt",
    }


def test_forwarded_visual_process_inference_result_dispatches_by_contract(monkeypatch) -> None:
    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []

    class AssistantService:
        def accept_worker_result(self, *, task_id, result):
            assert task_id == "vpa-inference-task"
            assert result["schema"] == "ananta.visual_process_assistant.inference_result.v1"
            return {"request_id": result["request_id"], "status": "completed"}

    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding._get_visual_process_assistant_service",
        lambda: AssistantService(),
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    response = {
        "schema": "ananta.visual_process_assistant.inference_result.v1",
        "task_id": "vpa-inference-task",
        "request_id": "vpa-request-a",
        "context_id": "ctx-prompt",
        "prompt_hash": "a" * 64,
        "status": "completed",
        "reason_code": None,
        "response": {},
        "model_metadata": {},
        "handler_contract": {"task_kind": "visual_process_assistant_inference"},
    }

    persist_forwarded_execution(
        tid="vpa-inference-task",
        response=response,
        task={
            "task_kind": "visual_process_assistant_inference",
            "history": [],
            "last_proposal": {},
            "verification_status": {},
        },
        request_data=SimpleNamespace(command=None),
    )

    assert updates[0][1]["verification_status"]["visual_process_assistant_request"] == {
        "request_id": "vpa-request-a",
        "status": "completed",
    }


def test_forwarded_visual_process_result_rejects_schema_kind_mismatch(monkeypatch) -> None:
    import pytest

    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []
    acceptance_calls = []
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding._get_visual_process_assistant_service",
        lambda: SimpleNamespace(
            accept_worker_result=lambda **kwargs: acceptance_calls.append(kwargs)
        ),
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="schema_kind_mismatch"):
        persist_forwarded_execution(
            tid="vpa-retrieval-task",
            response={
                "schema": "ananta.visual_process_assistant.inference_result.v1",
                "status": "completed",
            },
            task={
                "task_kind": "visual_process_assistant_retrieval",
                "history": [],
                "last_proposal": {},
                "verification_status": {},
            },
            request_data=SimpleNamespace(command=None),
        )

    assert acceptance_calls == []
    assert updates == []


def test_rejected_visual_process_acceptance_never_persists_readmodel(monkeypatch) -> None:
    import pytest

    from agent.services._task_scoped_forwarding import persist_forwarded_execution

    updates = []

    class AssistantService:
        def accept_worker_result(self, **_kwargs):
            raise ValueError("assistant_worker_context_binding_mismatch")

    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding._get_visual_process_assistant_service",
        lambda: AssistantService(),
    )
    monkeypatch.setattr(
        "agent.services._task_scoped_forwarding.update_local_task_status",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="context_binding_mismatch"):
        persist_forwarded_execution(
            tid="vpa-retrieval-task",
            response={
                "schema": "ananta.visual_process_assistant.retrieval_result.v1",
                "task_id": "vpa-retrieval-task",
                "request_id": "vpa-request-a",
                "context_id": "ctx-forged",
                "status": "completed",
                "evidence": [],
                "rejected_count": 0,
                "rejection_reasons": [],
                "consistency_state": "current",
            },
            task={
                "task_kind": "visual_process_assistant_retrieval",
                "history": [],
                "last_proposal": {},
                "verification_status": {},
            },
            request_data=SimpleNamespace(command=None),
        )

    assert updates == []
