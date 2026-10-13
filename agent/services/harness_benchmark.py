"""Canonical harness benchmark suite with a frozen holdout (SEH-013).

A suite is versioned and content-addressed, so every benchmark value is
traceable to an immutable dataset reference. Explore tasks are visible to the
evolution search; holdout tasks are kept out of it and can only be consumed by
the gate/benchmark authority, so the evolver cannot overfit the holdout.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ananta_contracts.harness_genome import content_digest

TASK_CLASSES = ("coding", "rag", "tool", "mcp", "planning", "delegation", "long_context", "failure_recovery",
                "security")
QUALITY_METRICS = ("task_correctness", "benchmark_score")
RESOURCE_METRICS = ("token_input", "token_output", "total_token_cost", "latency_p95", "context_peak_tokens")


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    task_id: str
    task_class: str
    dataset_version: str
    holdout: bool = False
    required_metrics: tuple[str, ...] = ()

    def assert_valid(self) -> None:
        for name, value in (("task_id", self.task_id), ("dataset_version", self.dataset_version)):
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError(f"benchmark_{name}_invalid")
        if self.task_class not in TASK_CLASSES:
            raise ValueError("benchmark_task_class_invalid")
        for metric in self.required_metrics:
            if metric not in QUALITY_METRICS + RESOURCE_METRICS:
                raise ValueError(f"benchmark_metric_unknown:{metric}")

    def to_mapping(self) -> dict:
        self.assert_valid()
        return {"task_id": self.task_id, "task_class": self.task_class, "dataset_version": self.dataset_version,
                "holdout": self.holdout, "required_metrics": list(self.required_metrics)}


@dataclass(frozen=True, slots=True)
class BenchmarkSuite:
    version: str
    tasks: tuple[BenchmarkTask, ...]
    suite_id: str = ""

    @classmethod
    def create(cls, *, version: str, tasks: Sequence[BenchmarkTask]) -> "BenchmarkSuite":
        if not isinstance(version, str) or not version or len(version) > 100:
            raise ValueError("benchmark_version_invalid")
        for task in tasks:
            task.assert_valid()
        ids = [task.task_id for task in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("benchmark_task_ids_duplicate")
        payload = [task.to_mapping() for task in tasks]
        return cls(version=version, tasks=tuple(tasks), suite_id=content_digest({"version": version, "tasks": payload}))

    def explore_tasks(self) -> tuple[BenchmarkTask, ...]:
        return tuple(task for task in self.tasks if not task.holdout)

    def holdout_tasks(self) -> tuple[BenchmarkTask, ...]:
        return tuple(task for task in self.tasks if task.holdout)

    def assert_search_visible(self, visible_task_ids: Iterable[str]) -> None:
        """A search view must never contain a holdout task id."""
        holdout = {task.task_id for task in self.holdout_tasks()}
        leaked = holdout & set(visible_task_ids)
        if leaked:
            raise ValueError(f"benchmark_holdout_leaked:{','.join(sorted(leaked))}")


@dataclass(frozen=True, slots=True)
class BaselineRecord:
    model: str
    harness_id: str
    metrics: Mapping[str, float]
    suite_id: str
    confidence: float = 1.0

    def assert_valid(self) -> None:
        if not self.model or not self.harness_id or not self.suite_id:
            raise ValueError("benchmark_baseline_identity_invalid")
        if not self.metrics:
            raise ValueError("benchmark_baseline_metrics_invalid")
        for metric, value in self.metrics.items():
            if not isinstance(metric, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("benchmark_baseline_metrics_invalid")


@dataclass
class BenchmarkRegistry:
    """In-memory port for immutable baseline records keyed by (model, harness, suite)."""

    _records: dict[tuple[str, str, str], BaselineRecord] = field(default_factory=dict)

    def record_baseline(self, record: BaselineRecord) -> None:
        record.assert_valid()
        key = (record.model, record.harness_id, record.suite_id)
        existing = self._records.get(key)
        if existing is not None and existing.metrics != record.metrics:
            raise ValueError("benchmark_baseline_conflict")
        self._records[key] = record

    def baseline(self, *, model: str, harness_id: str, suite_id: str) -> BaselineRecord | None:
        return self._records.get((model, harness_id, suite_id))

    def measures_quality_and_resources(self, metrics: Mapping[str, float]) -> bool:
        return any(metric in metrics for metric in QUALITY_METRICS) and any(
            metric in metrics for metric in RESOURCE_METRICS)
