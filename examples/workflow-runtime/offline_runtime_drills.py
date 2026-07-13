"""Native and LangGraph portions of the runtime-neutral example.

Both drills use production runtime classes with explicitly classified offline
ports.  They produce example evidence, never production promotion evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fake_provider import DeterministicFakeProvider

from agent.services.native_graph_orchestration_service import (
    NativeGraphOrchestrator,
    NativeGraphRequest,
)
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    SignedWorkflowCommand,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from worker.adapters.langgraph_execution_plan import (
    ExecutionPlanNodeOutcome,
    ExecutionPlanNodeRequest,
    LangGraphExecutionPlanRuntime,
    LangGraphParallelLimits,
)
from worker.adapters.workflow_budget import WorkflowBudgetGuard
from worker.runtime.native_graph import (
    HubTaskReceipt,
    NativeDelegatedNodeRuntime,
    NativeNodeCommand,
    NativeNodeResult,
)


class ExampleAllowPolicy:
    def authorize_command(self, command, *, plan, state):
        del command, plan, state
        return True, "example_policy_allow"

    def authorize_delegation(self, *, plan, node, state):
        del plan, node, state
        return True, "example_policy_allow"

    def allow_node(self, command):
        del command
        return True, "example_policy_allow"


class ExampleHubRevalidator:
    def revalidate(self, envelope):
        del envelope
        return True


class ExampleNativeHandler:
    def __init__(self, *, fail_once: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self._fail_once = set(fail_once or ())
        self._provider = DeterministicFakeProvider.from_default_fixture()

    def execute(self, command: NativeNodeCommand, *, hub_task_id: str) -> NativeNodeResult:
        node_id = command.node.node_id
        self.calls.append(node_id)
        response = (
            self._provider.next_response(node_id, operation_scope=command.run_id)
            if node_id in self._fail_once
            else self._provider.successful_response(node_id)
        )
        failed = response["status"] == "failed"
        return NativeNodeResult(
            result_id=f"example-native-result-{node_id}-{len(self.calls)}",
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status="failed" if failed else "completed",
            output_data={"value": response.get("value")},
            artifact_refs=(
                {}
                if failed
                else {artifact_id: str(response["artifact_ref"]) for artifact_id in command.node.output_artifacts}
            ),
            budget_usage={"tokens": 1, "cost_micros": 1},
            reason_code=str(response.get("reason_code") or ""),
        )


class ExampleImmediateHubQueue:
    """Example Hub port: records delegation before invoking one worker node."""

    def __init__(self, runtime: NativeDelegatedNodeRuntime) -> None:
        self.runtime = runtime
        self.submissions: list[NativeNodeCommand] = []
        self.results: dict[str, NativeNodeResult] = {}
        self.cancelled: list[str] = []

    def submit(self, command: NativeNodeCommand) -> HubTaskReceipt:
        self.submissions.append(command)
        task_id = f"example-hub-task-{len(self.submissions)}"
        self.results[task_id] = self.runtime.execute(command, hub_task_id=task_id)
        return HubTaskReceipt(task_id, command.command_id, True)

    def poll(self, *, tenant_id: str, run_id: str, hub_task_ids: tuple[str, ...]):
        del tenant_id, run_id
        values = []
        for task_id in hub_task_ids:
            result = self.results.pop(task_id, None)
            if result is not None:
                values.append(result)
        return tuple(values)

    def cancel(
        self,
        *,
        tenant_id: str,
        run_id: str,
        hub_task_ids: tuple[str, ...],
        reason: str,
    ) -> None:
        del tenant_id, run_id, reason
        self.cancelled.extend(hub_task_ids)


@dataclass
class NativeFixture:
    orchestrator: NativeGraphOrchestrator
    queue: ExampleImmediateHubQueue
    handler: ExampleNativeHandler
    keys: HmacKeyRing
    ledger: InMemorySideEffectLedger
    stores: dict[str, Any]

    def restarted(self) -> "NativeFixture":
        return NativeFixture(
            orchestrator=_native_orchestrator(
                queue=self.queue,
                stores=self.stores,
                ledger=self.ledger,
                keys=self.keys,
            ),
            queue=self.queue,
            handler=self.handler,
            keys=self.keys,
            ledger=self.ledger,
            stores=self.stores,
        )


def _native_fixture(*, fail_once: set[str] | None = None) -> NativeFixture:
    keys = HmacKeyRing({"example-native-key": b"n" * 32}, active_key_id="example-native-key")
    ledger = InMemorySideEffectLedger()
    handler = ExampleNativeHandler(fail_once=fail_once)
    worker = NativeDelegatedNodeRuntime(
        handler=handler,
        authorization_verifier=AuthorizationVerifier(
            keys,
            InMemoryReplayNonceStore(clock=lambda: 100.0),
        ),
        policy=ExampleAllowPolicy(),
        capabilities=frozenset(
            {
                "approval",
                "structured_output",
                "tool_calling",
            }
        ),
        ledger=ledger,
        hub_revalidator=ExampleHubRevalidator(),
        clock=lambda: 100.0,
    )
    queue = ExampleImmediateHubQueue(worker)
    stores = {
        "checkpoints": InMemoryCheckpointStore(),
        "events": InMemoryEventStore(),
        "ownership": InMemoryExecutionOwnershipStore(),
    }
    return NativeFixture(
        orchestrator=_native_orchestrator(
            queue=queue,
            stores=stores,
            ledger=ledger,
            keys=keys,
        ),
        queue=queue,
        handler=handler,
        keys=keys,
        ledger=ledger,
        stores=stores,
    )


def _native_orchestrator(
    *,
    queue: ExampleImmediateHubQueue,
    stores: dict[str, Any],
    ledger: InMemorySideEffectLedger,
    keys: HmacKeyRing,
) -> NativeGraphOrchestrator:
    return NativeGraphOrchestrator(
        queue=queue,
        checkpoints=stores["checkpoints"],
        events=stores["events"],
        ownership=stores["ownership"],
        ledger=ledger,
        key_ring=keys,
        command_verifier=WorkflowCommandVerifier(
            keys,
            InMemoryReplayNonceStore(clock=lambda: 100.0),
        ),
        policy=ExampleAllowPolicy(),
        clock=lambda: 100.0,
    )


def _native_request(plan: ExecutionPlan, run_id: str) -> NativeGraphRequest:
    return NativeGraphRequest(
        plan,
        run_id,
        f"example-control-{run_id}",
        tenant_parallel_limit=1,
        worker_parallel_limit=1,
    )


def _advance_until(orchestrator, request, statuses: set[str], *, limit: int = 12):
    result = None
    for _ in range(limit):
        result = orchestrator.advance(request)
        if result.status in statuses:
            return result
    raise RuntimeError(f"example_native_did_not_reach:{','.join(sorted(statuses))}")


def _native_command(
    fixture: NativeFixture,
    result,
    *,
    command_type: str,
    command_id: str,
    step_id: str,
    payload: dict[str, Any] | None = None,
) -> SignedWorkflowCommand:
    return SignedWorkflowCommand.issue(
        key_ring=fixture.keys,
        command_type=command_type,
        tenant_id=result.checkpoint.tenant_id,
        workflow_id=result.checkpoint.workflow_id,
        run_id=result.checkpoint.run_id,
        step_id=step_id,
        checkpoint_id=result.checkpoint.checkpoint_id,
        expected_revision=result.checkpoint.revision,
        plan_hash=result.checkpoint.plan_hash,
        policy_version=result.checkpoint.policy_version,
        actor_id="example-operator",
        actor_roles=("operator",),
        payload=dict(payload or {}),
        now=100.0,
        command_id=command_id,
        nonce=f"{command_id}-nonce",
    )


def run_native_drill(plan: ExecutionPlan) -> dict[str, Any]:
    fixture = _native_fixture(fail_once={"draft"})
    request = _native_request(plan, "example-native-main-v1")
    fixture.orchestrator.start(request)
    waiting = _advance_until(
        fixture.orchestrator,
        request,
        {"waiting_for_approval"},
    )
    before_restart_revision = waiting.checkpoint.revision
    fixture = fixture.restarted()
    approved = fixture.orchestrator.resume(
        request,
        command=_native_command(
            fixture,
            waiting,
            command_type="approve",
            command_id="example-native-approve-v1",
            step_id="publish",
        ),
    )
    completed = (
        approved if approved.status == "completed" else _advance_until(fixture.orchestrator, request, {"completed"})
    )
    events = [event.event_type for event in fixture.orchestrator.stream(request)]

    cancel_fixture = _native_fixture()
    cancel_request = _native_request(plan, "example-native-cancel-v1")
    running = cancel_fixture.orchestrator.start(cancel_request)
    cancelled = cancel_fixture.orchestrator.resume(
        cancel_request,
        command=_native_command(
            cancel_fixture,
            running,
            command_type="cancel",
            command_id="example-native-cancel-v1",
            step_id="__workflow__",
            payload={"reason": "example_operator_cancelled"},
        ),
    )
    if completed.status != "completed" or cancelled.status != "cancelled":
        raise RuntimeError("example_native_scenario_failed")
    expected = {
        "workflow.step.retry_scheduled",
        "workflow.approval.requested",
        "workflow.approval.granted",
        "workflow.run.completed",
    }
    if not expected.issubset(set(events)):
        raise RuntimeError("example_native_events_incomplete")
    return {
        "runtime_path": "agent.services.native_graph_orchestration_service.NativeGraphOrchestrator",
        "plan_hash": plan.plan_hash,
        "classification": "real_runtime_with_example_ports",
        "durable": False,
        "scenarios": {
            "failure": {
                "status": "observed",
                "reason_code": "example_fake_transient_failure",
                "event": "workflow.step.retry_scheduled",
                "attempts": fixture.handler.calls.count("draft"),
            },
            "approval": {
                "status": "observed",
                "events": ["workflow.approval.requested", "workflow.approval.granted"],
            },
            "cancel": {
                "status": "observed",
                "terminal_status": cancelled.status,
            },
            "crash": {
                "status": "process_reconstruction_probe",
                "production_equivalent": False,
                "checkpoint_revision": before_restart_revision,
            },
            "resume": {
                "status": "observed",
                "terminal_status": completed.status,
                "completed_nodes": list(completed.completed_node_ids),
            },
        },
        "event_types": sorted(set(events)),
        "delegated_nodes": [command.node.node_id for command in fixture.queue.submissions],
    }


class ExampleLangGraphExecutor:
    def __init__(self, *, fail_nodes: set[str] | None = None) -> None:
        self._fail_nodes = set(fail_nodes or ())
        self._provider = DeterministicFakeProvider.from_default_fixture()

    def execute(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome:
        del budget
        node_id = request.node.node_id
        response = (
            self._provider.response_for(node_id, attempt=1)
            if node_id in self._fail_nodes
            else self._provider.successful_response(node_id)
        )
        if response["status"] == "failed":
            return ExecutionPlanNodeOutcome.failed(str(response["reason_code"]))
        value = {
            "node_id": node_id,
            "dependencies": sorted(request.dependency_results),
            "value": response.get("value"),
        }
        return ExecutionPlanNodeOutcome.completed(
            value,
            artifacts={artifact_id: str(response["artifact_ref"]) for artifact_id in request.node.output_artifacts},
            tokens=0 if request.node.budget and request.node.budget.max_tokens == 0 else 1,
            cost_micros=0 if request.node.budget and request.node.budget.max_cost_micros == 0 else 1,
        )


def _langgraph_execute(
    plan: ExecutionPlan,
    *,
    fail_nodes: set[str] | None = None,
    approved: bool = False,
    cancelled: bool = False,
):
    runtime = LangGraphExecutionPlanRuntime(node_executor=ExampleLangGraphExecutor(fail_nodes=fail_nodes))
    return runtime.execute(
        plan=plan,
        workflow_input={"example": "runtime-neutral-publication"},
        execution_payload={"provider": "deterministic_fake"},
        limits=LangGraphParallelLimits(plan=1, tenant=1, worker=1),
        approved_gates=(frozenset(gate.gate_id for gate in plan.gates) if approved else frozenset()),
        cancel_requested=lambda: cancelled,
        budget=WorkflowBudgetGuard(
            max_steps=30,
            max_tokens=plan.budget.max_tokens,
            timeout_seconds=plan.budget.timeout_seconds,
        ),
        checkpointer=None,
        thread_id="example-langgraph-v1",
        recursion_limit=30,
    )


def run_langgraph_drill(plan: ExecutionPlan) -> dict[str, Any]:
    failure = _langgraph_execute(plan, fail_nodes={"draft"}, approved=True)
    waiting = _langgraph_execute(plan)
    completed = _langgraph_execute(plan, approved=True)
    cancelled = _langgraph_execute(plan, approved=True, cancelled=True)
    if (
        failure.status != "failed"
        or waiting.status != "blocked"
        or completed.status != "completed"
        or cancelled.status != "cancelled"
    ):
        raise RuntimeError("example_langgraph_scenario_failed")
    return {
        "runtime_path": "worker.adapters.langgraph_execution_plan.LangGraphExecutionPlanRuntime",
        "framework_path": "langgraph.graph.StateGraph",
        "plan_hash": plan.plan_hash,
        "classification": "real_runtime_with_example_node_executor",
        "durable": False,
        "scenarios": {
            "failure": {
                "status": "observed",
                "reason_code": failure.failed_nodes.get("draft"),
            },
            "approval": {
                "status": "observed",
                "blocked_reason": waiting.reason_code,
                "approved_terminal_status": completed.status,
            },
            "cancel": {
                "status": "observed",
                "terminal_status": cancelled.status,
            },
            "crash": {
                "status": "not_durable_by_example_design",
                "production_equivalent": False,
            },
            "resume": {
                "status": "adapter_reinvocation_only",
                "production_equivalent": False,
            },
        },
        "trace": list(completed.canonical_trace()),
        "artifacts": sorted(completed.artifacts),
    }


__all__ = [
    "ExampleImmediateHubQueue",
    "ExampleLangGraphExecutor",
    "ExampleNativeHandler",
    "run_langgraph_drill",
    "run_native_drill",
]
