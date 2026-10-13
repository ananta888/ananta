"""SEH-013: immutable benchmark suite and hidden holdout."""

from __future__ import annotations

import pytest

from agent.services.harness_benchmark import (
    BaselineRecord,
    BenchmarkRegistry,
    BenchmarkSuite,
    BenchmarkTask,
)


def task(task_id, task_class="coding", *, holdout=False, metrics=("task_correctness", "token_input")):
    return BenchmarkTask(task_id=task_id, task_class=task_class, dataset_version="ds-1", holdout=holdout,
                         required_metrics=metrics)


def suite():
    return BenchmarkSuite.create(version="v1", tasks=[
        task("t1"), task("t2", "rag"), task("h1", holdout=True), task("h2", "security", holdout=True)])


def test_suite_is_content_addressed_and_splits_explore_from_holdout():
    value = suite()
    assert value.suite_id == suite().suite_id
    assert {t.task_id for t in value.explore_tasks()} == {"t1", "t2"}
    assert {t.task_id for t in value.holdout_tasks()} == {"h1", "h2"}
    assert value.suite_id != BenchmarkSuite.create(version="v2", tasks=value.tasks).suite_id


def test_search_view_must_not_leak_the_holdout():
    value = suite()
    value.assert_search_visible(["t1", "t2"])  # ok
    with pytest.raises(ValueError, match="benchmark_holdout_leaked:h1"):
        value.assert_search_visible(["t1", "h1"])


@pytest.mark.parametrize("bad", [
    {"task_id": ""}, {"dataset_version": ""}, {"task_class": "unknown"},
    {"required_metrics": ("mystery_metric",)},
])
def test_invalid_task_is_rejected(bad):
    kwargs = dict(task_id="t", task_class="coding", dataset_version="ds-1")
    kwargs.update(bad)
    with pytest.raises(ValueError):
        BenchmarkTask(**kwargs).assert_valid()


def test_duplicate_task_ids_are_rejected():
    with pytest.raises(ValueError, match="benchmark_task_ids_duplicate"):
        BenchmarkSuite.create(version="v1", tasks=[task("t1"), task("t1", "rag")])


def test_baselines_are_keyed_and_conflict_safe():
    registry = BenchmarkRegistry()
    record = BaselineRecord(model="bonsai-27b", harness_id="baseline", suite_id=suite().suite_id,
                            metrics={"task_correctness": 0.8, "token_input": 12000})
    registry.record_baseline(record)
    assert registry.baseline(model="bonsai-27b", harness_id="baseline", suite_id=suite().suite_id) == record
    assert registry.baseline(model="other", harness_id="baseline", suite_id=suite().suite_id) is None
    with pytest.raises(ValueError, match="benchmark_baseline_conflict"):
        registry.record_baseline(BaselineRecord(model="bonsai-27b", harness_id="baseline",
                                                suite_id=suite().suite_id,
                                                metrics={"task_correctness": 0.5, "token_input": 1}))


def test_quality_and_resource_metrics_are_required_together():
    registry = BenchmarkRegistry()
    assert registry.measures_quality_and_resources({"task_correctness": 0.9, "token_input": 10}) is True
    assert registry.measures_quality_and_resources({"task_correctness": 0.9}) is False
    assert registry.measures_quality_and_resources({"token_input": 10}) is False
