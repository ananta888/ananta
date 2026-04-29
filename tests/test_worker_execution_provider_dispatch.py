from __future__ import annotations

from agent.providers.interfaces import ProviderDescriptor
from agent.providers.registry import GenericProviderRegistry
from agent.providers.worker_execution import (
    WorkerExecutionProviderResult,
    WorkerExecutionRequest,
    WorkerExecutorDispatchBridge,
    register_default_worker_execution_descriptors,
)


class _MockWorkerExecutionProvider:
    descriptor = ProviderDescriptor(
        provider_id="opencode",
        provider_family="worker_execution",
        capabilities=("worker_job_execute",),
        risk_class="high",
        enabled_by_default=False,
    )

    def execute(self, request: WorkerExecutionRequest) -> WorkerExecutionProviderResult:
        return WorkerExecutionProviderResult(
            status="completed",
            reason="executed",
            artifacts=[{"artifact_type": "worker_result", "artifact_ref": f"task:{request.task_id}"}],
            result_payload={"executor_kind": request.executor_kind},
        )


def _request(executor_kind: str) -> WorkerExecutionRequest:
    return WorkerExecutionRequest(task_id="task-1", executor_kind=executor_kind)


def test_dispatch_bridge_marks_unknown_or_disabled_provider_as_degraded() -> None:
    registry = GenericProviderRegistry()
    register_default_worker_execution_descriptors(registry)
    bridge = WorkerExecutorDispatchBridge(registry)

    result = bridge.dispatch(executor_kind="opencode", request=_request("opencode"))
    assert result.status == "degraded"
    assert result.result_payload["dispatch_status"] in {"disabled", "degraded"}


def test_dispatch_bridge_maps_executor_kind_to_registry_provider_and_executes() -> None:
    registry = GenericProviderRegistry()
    register_default_worker_execution_descriptors(registry)
    registry.register_factory(
        provider_family="worker_execution",
        provider_id="opencode",
        factory=_MockWorkerExecutionProvider,
    )
    bridge = WorkerExecutorDispatchBridge(registry)

    result = bridge.dispatch(executor_kind="opencode", request=_request("opencode"), enable_provider=True)
    assert result.status == "completed"
    assert result.result_payload["executor_kind"] == "opencode"
    assert result.artifacts[0]["artifact_type"] == "worker_result"


def test_dispatch_bridge_reports_missing_dependency_as_degraded() -> None:
    registry = GenericProviderRegistry()
    register_default_worker_execution_descriptors(registry)

    def _factory() -> _MockWorkerExecutionProvider:
        raise ModuleNotFoundError("opencode")

    registry.register_factory(provider_family="worker_execution", provider_id="opencode", factory=_factory)
    bridge = WorkerExecutorDispatchBridge(registry)

    result = bridge.dispatch(executor_kind="opencode", request=_request("opencode"), enable_provider=True)
    assert result.status == "degraded"
    assert "missing_optional_dependency" in str(result.reason or "")
