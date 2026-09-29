from __future__ import annotations

import pytest

from agent.services.workflow_runtime.condition_evaluator import DeclarativeConditionEvaluator
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.parallel import (
    BoundedFanOutScheduler,
    BranchResult,
    DeterministicMergeService,
)


def plan() -> ExecutionPlan:
    return ExecutionPlan.from_mapping(
        {
            "tenant_id": "tenant-1",
            "plan_id": "parallel-v1",
            "workflow_id": "parallel",
            "policy_version": "policy-v1",
            "capabilities": ["bounded_parallel", "deterministic_merge"],
            "nodes": [
                {"id": "a", "metadata": {"parallel_group": "g", "parallel_limit": 2}},
                {"id": "b", "metadata": {"parallel_group": "g", "parallel_limit": 2}},
                {
                    "id": "c",
                    "node_type": "merge",
                    "metadata": {
                        "merge_strategy": "ordered-by-node-id",
                        "partial_failure": "fail",
                    },
                },
            ],
            "edges": [
                {"from": "a", "to": "c"},
                {"from": "b", "to": "c"},
            ],
        }
    )


def test_condition_unknown_and_type_mismatch_are_explicit() -> None:
    evaluator = DeclarativeConditionEvaluator()

    unknown = evaluator.evaluate({"op": "eq", "field": "result.score", "value": 1}, {})
    mismatch = evaluator.evaluate(
        {"op": "eq", "field": "result.score", "value": "1"},
        {"result": {"score": 1}},
    )

    assert unknown.value is None
    assert unknown.reason_code == "condition_field_unknown"
    assert mismatch.value is None
    assert mismatch.reason_code == "condition_type_mismatch"


def test_condition_rejects_expression_and_template_paths() -> None:
    evaluator = DeclarativeConditionEvaluator()

    assert evaluator.evaluate({"op": "python", "value": "exec('x')"}, {}).matches is False
    assert evaluator.evaluate({"op": "exists", "field": "__class__.__mro__"}, {}).matches is False
    assert evaluator.evaluate({"op": "exists", "field": "{{ secrets }}"}, {}).matches is False


def test_nested_conditions_use_three_valued_logic() -> None:
    evaluator = DeclarativeConditionEvaluator()
    condition = {
        "op": "all",
        "conditions": [
            {"op": "eq", "field": "status", "value": "ready"},
            {"op": "not", "condition": {"op": "exists", "field": "error"}},
        ],
    }

    result = evaluator.evaluate(condition, {"status": "ready"})

    assert result.value is True


def test_condition_depth_and_node_limits_fail_closed_without_recursion_error() -> None:
    evaluator = DeclarativeConditionEvaluator(maximum_depth=4, maximum_nodes=5)
    nested: dict = {"op": "always"}
    for _ in range(100):
        nested = {"op": "not", "condition": nested}

    deep = evaluator.evaluate(nested, {})
    wide = evaluator.evaluate(
        {"op": "all", "conditions": [{"op": "always"} for _ in range(20)]},
        {},
    )

    assert deep.value is None and deep.reason_code == "condition_depth_exceeded"
    assert wide.value is None and wide.reason_code == "condition_node_limit_exceeded"


def test_condition_fuzz_corpus_never_executes_or_raises() -> None:
    evaluator = DeclarativeConditionEvaluator(maximum_depth=8, maximum_nodes=32)
    corpus = [None, []] + [
        {"op": value, "field": value, "value": value}
        for value in ("", "python", "${7*7}", "__class__", "a" * 10_000)
    ]
    for candidate in corpus:
        result = evaluator.evaluate(candidate, {"safe": True})  # type: ignore[arg-type]
        assert result.value in {True, False, None}


def test_fan_out_is_bounded_and_deterministic() -> None:
    scheduler = BoundedFanOutScheduler()

    batch = scheduler.select_ready(
        plan(),
        completed_node_ids=set(),
        tenant_limit=2,
        worker_limit=3,
    )

    assert [candidate.node_id for candidate in batch.candidates] == ["a", "b"]
    assert batch.effective_limit == 2


def test_merge_waits_for_dependencies_and_orders_by_node_id() -> None:
    scheduler = BoundedFanOutScheduler()

    batch = scheduler.select_ready(
        plan(),
        completed_node_ids={"a", "b"},
        tenant_limit=2,
        worker_limit=2,
    )
    merged = DeterministicMergeService().merge(
        [
            BranchResult("b", "completed", {"value": 2}),
            BranchResult("a", "completed", {"value": 1}),
        ],
        strategy="ordered-by-node-id",
    )

    assert [candidate.node_id for candidate in batch.candidates] == ["c"]
    assert merged.value == [{"value": 1}, {"value": 2}]


def test_partial_failure_is_never_synthetic_success_by_default() -> None:
    merged = DeterministicMergeService().merge(
        [BranchResult("a", "completed", 1), BranchResult("b", "failed", reason_code="timeout")],
        strategy="ordered-by-node-id",
    )

    assert merged.status == "failed"
    assert merged.failed_branches == ("b",)


def test_merge_policy_is_validated_before_branch_status_is_interpreted() -> None:
    service = DeterministicMergeService()

    with pytest.raises(ValueError, match="partial_failure_policy_invalid"):
        service.merge(
            [BranchResult("a", "failed", reason_code="timeout")],
            strategy="ordered-by-node-id",
            partial_failure="pretend-success",
        )
    with pytest.raises(ValueError, match="strategy_unsupported"):
        service.merge(
            [BranchResult("a", "completed", 1)],
            strategy="completion-order",
        )


def test_plan_limit_and_running_group_capacity_are_both_enforced() -> None:
    scheduler = BoundedFanOutScheduler()
    value = plan()
    limited = ExecutionPlan.from_mapping(
        {**value.to_dict(include_hash=False), "metadata": {"parallel_limit": 1}}
    )
    group_raw = value.to_dict(include_hash=False)
    group_raw["nodes"][0]["metadata"]["parallel_limit"] = 1
    group_raw["nodes"][1]["metadata"]["parallel_limit"] = 1
    group_limited_plan = ExecutionPlan.from_mapping(group_raw)

    plan_limited = scheduler.select_ready(
        limited,
        completed_node_ids=set(),
        tenant_limit=4,
        worker_limit=4,
    )
    group_limited = scheduler.select_ready(
        group_limited_plan,
        completed_node_ids=set(),
        running_node_ids={"a"},
        tenant_limit=3,
        worker_limit=3,
    )

    assert [candidate.node_id for candidate in plan_limited.candidates] == ["a"]
    assert group_limited.candidates == ()
    assert group_limited.deferred_node_ids == ("b",)
