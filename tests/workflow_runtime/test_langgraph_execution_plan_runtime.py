from __future__ import annotations

import json
import threading
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph.graph", reason="pinned LangGraph runtime extra is not installed")

from agent.providers.lc_lg import LangGraphProviderConfig
from agent.services.native_graph_orchestration_service import (
    NativeGraphOrchestrator,
    NativeGraphRequest,
)
from agent.services.workflow_runtime import (
    HmacKeyRing,
    InMemoryCheckpointStore,
    InMemoryEventStore,
    InMemoryExecutionOwnershipStore,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.components import (
    WorkflowComponent,
    WorkflowComponentCompiler,
    WorkflowComponentRegistry,
)
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.native_graph_contracts import HubTaskReceipt
from agent.services.workflow_runtime.parallel import DeterministicMergeService, MergeResult
from worker.adapters.langgraph_adapter import LangGraphAdapter
from worker.adapters.langgraph_execution_plan import (
    ExecutionPlanNodeOutcome,
    ExecutionPlanNodeRequest,
    LangGraphExecutionPlanRuntime,
    LangGraphParallelLimits,
)
from worker.adapters.workflow_budget import WorkflowBudgetGuard

assert version("langgraph") == "0.2.76"

_GOLDEN = Path(__file__).parent / "golden" / "langgraph_parallel_merge.v1.json"


def _parallel_plan(
    *,
    partial_failure: str = "fail",
    branch_failure_policy: str = "fail",
    plan_parallel_limit: int | None = None,
    max_tokens: int = 100,
    estimated_tokens: int = 0,
) -> ExecutionPlan:
    plan_metadata: dict[str, Any] = {"reference_scenario": "bounded-parallel-merge"}
    if plan_parallel_limit is not None:
        plan_metadata["parallel_limit"] = plan_parallel_limit
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-a",
            "plan_id": "parallel-plan-v1",
            "workflow_id": "parallel-workflow",
            "policy_version": "policy-v1",
            "capabilities": ["bounded_parallel", "deterministic_merge"],
            "nodes": [
                {
                    "id": "branch-b",
                    "task_kind": "analysis",
                    "required_capabilities": ["bounded_parallel"],
                    "output_artifacts": ["branch-b-result"],
                    "metadata": {
                        "parallel_group": "analysis",
                        "parallel_limit": 2,
                        "failure_policy": branch_failure_policy,
                        "estimated_tokens": estimated_tokens,
                    },
                },
                {
                    "id": "branch-a",
                    "task_kind": "analysis",
                    "required_capabilities": ["bounded_parallel"],
                    "output_artifacts": ["branch-a-result"],
                    "metadata": {
                        "parallel_group": "analysis",
                        "parallel_limit": 2,
                        "failure_policy": branch_failure_policy,
                        "estimated_tokens": estimated_tokens,
                    },
                },
                {
                    "id": "merge",
                    "node_type": "merge",
                    "task_kind": "merge",
                    "required_capabilities": ["deterministic_merge"],
                    "input_artifacts": ["branch-a-result", "branch-b-result"],
                    "output_artifacts": ["merged-result"],
                    "metadata": {
                        "merge_strategy": "ordered-by-node-id",
                        "partial_failure": partial_failure,
                    },
                },
            ],
            "edges": [
                {"from": "branch-a", "to": "merge"},
                {"from": "branch-b", "to": "merge"},
            ],
            "artifacts": [
                {"id": "branch-a-result"},
                {"id": "branch-b-result", "required": partial_failure != "omit"},
                {"id": "merged-result"},
            ],
            "budget": {
                "max_attempts": 1,
                "timeout_seconds": 30,
                "max_tokens": max_tokens,
                "max_cost_micros": 100,
            },
            "metadata": plan_metadata,
        }
    )


class _Executor:
    def __init__(
        self,
        *,
        barrier: threading.Barrier | None = None,
        failures: set[str] | None = None,
        tokens: int = 0,
        cancellation: threading.Event | None = None,
        cancel_after: str = "",
    ) -> None:
        self._barrier = barrier
        self._failures = set(failures or ())
        self._tokens = tokens
        self._cancellation = cancellation
        self._cancel_after = cancel_after
        self.calls: list[str] = []
        self.completion_order: list[str] = []
        self._lock = threading.Lock()

    def execute(
        self,
        request: ExecutionPlanNodeRequest,
        *,
        budget: WorkflowBudgetGuard,
    ) -> ExecutionPlanNodeOutcome:
        del budget
        node_id = request.node.node_id
        with self._lock:
            self.calls.append(node_id)
        if self._barrier is not None and node_id.startswith("branch-"):
            self._barrier.wait(timeout=2)
            time.sleep(0.03 if node_id == "branch-a" else 0.001)
        if self._cancellation is not None and node_id == self._cancel_after:
            self._cancellation.set()
        with self._lock:
            self.completion_order.append(node_id)
        if node_id in self._failures:
            return ExecutionPlanNodeOutcome.failed(f"scripted_failure:{node_id}")
        return ExecutionPlanNodeOutcome.completed(
            {"branch": node_id.removeprefix("branch-")},
            tokens=self._tokens,
        )


def _run(
    plan: ExecutionPlan,
    executor: _Executor,
    *,
    limits: LangGraphParallelLimits | None = None,
    approved_gates: frozenset[str] = frozenset(),
    cancel_requested=None,
):
    return LangGraphExecutionPlanRuntime(node_executor=executor).execute(
        plan=plan,
        workflow_input={"query": "offline"},
        execution_payload={},
        limits=limits or LangGraphParallelLimits(plan=2, tenant=2, worker=2),
        approved_gates=approved_gates,
        cancel_requested=cancel_requested,
        budget=WorkflowBudgetGuard(max_steps=20, timeout_seconds=30),
        thread_id="test-thread",
        recursion_limit=10,
    )


def test_real_langgraph_fanout_runs_concurrently_and_merges_stably_ten_times() -> None:
    artifacts: list[str] = []
    for _ in range(10):
        executor = _Executor(barrier=threading.Barrier(2))
        result = _run(_parallel_plan(), executor)

        assert result.status == "completed"
        assert result.batches == (("branch-a", "branch-b"), ("merge",))
        assert result.max_observed_parallelism == 2
        assert executor.completion_order[:2] == ["branch-b", "branch-a"]
        assert result.node_results["merge"] == [{"branch": "a"}, {"branch": "b"}]
        artifacts.append(json.dumps(result.to_artifact(), sort_keys=True, separators=(",", ":")))

    assert len(set(artifacts)) == 1


@pytest.mark.parametrize(
    ("limits", "plan_limit", "expected_batches", "expected_parallelism"),
    [
        (LangGraphParallelLimits(2, 1, 3), None, (("branch-a",), ("branch-b",), ("merge",)), 1),
        (LangGraphParallelLimits(3, 3, 3), 1, (("branch-a",), ("branch-b",), ("merge",)), 1),
    ],
)
def test_plan_tenant_and_worker_limits_narrow_static_supersteps(
    limits: LangGraphParallelLimits,
    plan_limit: int | None,
    expected_batches: tuple[tuple[str, ...], ...],
    expected_parallelism: int,
) -> None:
    result = _run(
        _parallel_plan(plan_parallel_limit=plan_limit),
        _Executor(),
        limits=limits,
    )

    assert result.status == "completed"
    assert result.batches == expected_batches
    assert result.max_observed_parallelism == expected_parallelism


@pytest.mark.parametrize(
    ("partial_failure", "expected_status", "expected_merge"),
    [
        ("fail", "failed", None),
        ("omit", "partial", [{"branch": "a"}]),
    ],
)
def test_partial_failure_policy_is_explicit_and_uses_neutral_merge(
    partial_failure: str,
    expected_status: str,
    expected_merge: list[dict[str, str]] | None,
) -> None:
    result = _run(
        _parallel_plan(
            partial_failure=partial_failure,
            branch_failure_policy="continue",
        ),
        _Executor(failures={"branch-b"}),
    )

    assert result.status == expected_status
    merge = next(record for record in result.records if record.node_id == "merge")
    assert merge.failed_branches == ("branch-b",)
    assert merge.value == expected_merge
    if partial_failure == "fail":
        assert result.reason_code == "merge_branch_failed"
    else:
        assert result.reason_code == "merge_partial"


def test_cancel_before_start_and_between_batches_never_starts_open_branch() -> None:
    before = threading.Event()
    before.set()
    untouched = _Executor()
    cancelled = _run(_parallel_plan(), untouched, cancel_requested=before.is_set)

    assert cancelled.status == "cancelled"
    assert untouched.calls == []

    during = threading.Event()
    executor = _Executor(cancellation=during, cancel_after="branch-a")
    raced = _run(
        _parallel_plan(),
        executor,
        limits=LangGraphParallelLimits(1, 1, 1),
        cancel_requested=during.is_set,
    )

    assert raced.status == "cancelled"
    assert executor.calls == ["branch-a"]
    assert next(record for record in raced.records if record.node_id == "branch-b").status == "cancelled"


def test_approval_preflight_blocks_every_branch_until_bound_gate_is_approved() -> None:
    plan = _parallel_plan()
    raw = plan.to_dict(include_hash=False)
    raw["nodes"][0]["gate_id"] = "gate-a"
    raw["gates"] = [{"id": "gate-a"}]
    gated = ExecutionPlan.from_mapping(raw)
    executor = _Executor()

    blocked = _run(gated, executor)
    assert blocked.status == "blocked"
    assert blocked.reason_code == "approval_required:gate-a"
    assert executor.calls == []

    completed = _run(gated, executor, approved_gates=frozenset({"gate-a"}))
    assert completed.status == "completed"
    assert set(executor.calls) == {"branch-a", "branch-b"}


def test_declared_and_actual_budget_exhaustion_fail_closed() -> None:
    declared_executor = _Executor()
    declared = _run(
        _parallel_plan(max_tokens=5, estimated_tokens=3),
        declared_executor,
    )
    assert declared.status == "failed"
    assert declared.reason_code == "langgraph_plan_budget_exceeded:declared_tokens"
    assert declared_executor.calls == []

    actual = _run(_parallel_plan(max_tokens=10), _Executor(tokens=6))
    assert actual.status == "failed"
    assert actual.reason_code == "langgraph_plan_budget_exceeded:tokens"


def _component_compiler() -> tuple[WorkflowComponentCompiler, ExecutionPlan]:
    component_plan = ExecutionPlan.from_mapping(
        {
            "tenant_id": "component-tenant",
            "plan_id": "component-plan",
            "workflow_id": "component-workflow",
            "policy_version": "policy-v1",
            "nodes": [{"id": "inner", "output_artifacts": ["component-output"]}],
            "artifacts": [{"id": "component-output"}],
        }
    )
    component = WorkflowComponent(
        component_id="summary",
        version="1.1.0",
        compatible_versions=("1.0.0",),
        policy_version="policy-v1",
        plan=component_plan,
        output_schema={
            "type": "object",
            "required": ["branch"],
            "properties": {"branch": {"type": "string"}},
            "additionalProperties": False,
        },
        output_artifacts=("component-output",),
        artifact_contract={"type": "object"},
    )
    registry = WorkflowComponentRegistry()
    registry.register(component)
    compiler = WorkflowComponentCompiler(registry)
    root = ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-a",
            "plan_id": "component-root",
            "workflow_id": "component-root",
            "policy_version": "policy-v1",
            "nodes": [
                {
                    "id": "reuse",
                    "node_type": "component",
                    "output_artifacts": ["final"],
                    "metadata": {
                        "component": {"id": "summary", "version": "1.0.0"},
                        "component_input": {},
                    },
                }
            ],
            "artifacts": [{"id": "final"}],
        }
    )
    return compiler, root


def test_n_minus_one_component_is_flattened_and_output_schema_is_enforced() -> None:
    compiler, root = _component_compiler()
    compiled = compiler.compile(root)

    assert compiled.metadata["compiled_components"] == {"reuse": "summary@1.1.0"}
    valid = _run(compiled, _Executor())
    assert valid.status == "completed"
    assert [record.node_id for record in valid.records] == ["reuse/inner"]

    class InvalidExecutor(_Executor):
        def execute(self, request, *, budget):
            del request, budget
            return ExecutionPlanNodeOutcome.completed("not-an-object")

    invalid = _run(compiled, InvalidExecutor())
    assert invalid.status == "failed"
    assert invalid.reason_code == "workflow_component_output_invalid"


class _CompileOnlyNativeQueue:
    def submit(self, command):
        return HubTaskReceipt("", command.command_id, False, "compile_parity_stop")

    def poll(self, **_values):
        return ()

    def cancel(self, **_values):
        return None


class _CompileParityPolicy:
    def authorize_command(self, command, *, plan, state):
        del command, plan, state
        return True, "allowed"

    def authorize_delegation(self, *, plan, node, state):
        del plan, node, state
        return True, "allowed"


def test_native_and_langgraph_compile_identical_n_minus_one_component_plan() -> None:
    compiler, root = _component_compiler()
    expected = compiler.compile(root)
    keys = HmacKeyRing({"component-parity": "p" * 32}, active_key_id="component-parity")
    native = NativeGraphOrchestrator(
        queue=_CompileOnlyNativeQueue(),
        checkpoints=InMemoryCheckpointStore(),
        events=InMemoryEventStore(),
        ownership=InMemoryExecutionOwnershipStore(),
        ledger=InMemorySideEffectLedger(),
        key_ring=keys,
        command_verifier=WorkflowCommandVerifier(keys, InMemoryReplayNonceStore()),
        policy=_CompileParityPolicy(),
        component_compiler=compiler,
    )

    native_result = native.start(
        NativeGraphRequest(
            plan=root,
            run_id="component-parity-native",
            control_task_id="component-parity-control",
        )
    )
    langgraph = LangGraphAdapter(
        LangGraphProviderConfig(enabled=True, mode="dry_run"),
        component_compiler=compiler,
    ).dry_run(
        task_id="component-parity-langgraph",
        task_type="agent_workflow",
        payload={"execution_plan": root.to_dict()},
    )

    assert native_result.effective_plan is not None
    assert native_result.effective_plan.to_dict() == expected.to_dict()
    assert langgraph.metadata["plan_hash"] == expected.plan_hash
    assert langgraph.metadata["compiled_components"] == {"reuse": "summary@1.1.0"}
    assert [step["node_id"] for step in langgraph.plan_steps] == ["reuse/inner"]


class _ReversingMerge(DeterministicMergeService):
    def merge(self, results, *, strategy, partial_failure="fail"):
        valid = super().merge(results, strategy=strategy, partial_failure=partial_failure)
        if valid.status != "completed" or not isinstance(valid.value, list):
            return valid
        return MergeResult(
            status=valid.status,
            value=list(reversed(valid.value)),
            failed_branches=valid.failed_branches,
            reason_code=valid.reason_code,
        )


def test_golden_artifact_detects_a_faulty_merge_implementation() -> None:
    plan = _parallel_plan()
    correct = _run(plan, _Executor(barrier=threading.Barrier(2))).to_artifact()
    faulty = (
        LangGraphExecutionPlanRuntime(
            node_executor=_Executor(),
            merge=_ReversingMerge(),
        )
        .execute(
            plan=plan,
            workflow_input={},
            execution_payload={},
            limits=LangGraphParallelLimits(2, 2, 2),
            budget=WorkflowBudgetGuard(max_steps=20, timeout_seconds=30),
            thread_id="faulty",
            recursion_limit=10,
        )
        .to_artifact()
    )

    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert correct == golden
    assert faulty != golden
    assert correct["records"][-1]["value"] == [{"branch": "a"}, {"branch": "b"}]
    assert faulty["records"][-1]["value"] == [{"branch": "b"}, {"branch": "a"}]


def test_adapter_executes_neutral_plan_without_task_or_worker_orchestration() -> None:
    executor = _Executor(barrier=threading.Barrier(2))
    adapter = LangGraphAdapter(
        LangGraphProviderConfig(
            enabled=True,
            mode="local_live",
            checkpoint_policy="local_ephemeral",
            state_policy="ephemeral",
            max_iterations=10,
            allowed_tools=[],
        ),
        execution_plan_node_executor=executor,
    )
    plan = _parallel_plan()
    result = adapter.execute(
        task_id="hub-task-1",
        task_type="agent_workflow",
        payload={
            "execution_plan": plan.to_dict(),
            "parallel_limits": {"plan": 2, "tenant": 2, "worker": 2},
        },
    )

    assert result.status == "success"
    assert result.artifacts[0]["max_observed_parallelism"] == 2
    assert set(executor.calls) == {"branch-a", "branch-b"}
