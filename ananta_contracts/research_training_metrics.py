"""Normalized, content-free research telemetry contract."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ananta_contracts.research_training import ResearchTrainingContractError, canonical_digest, require_id

RESEARCH_METRICS = frozenset(
    {
        "train_loss",
        "validation_loss",
        "bits_per_byte",
        "tokens_per_second",
        "mfu_estimate",
        "flops_estimate",
        "peak_memory_bytes",
        "learning_rate",
        "gradient_norm",
        "checkpoint_seconds",
        "accuracy",
        "reward_mean",
        "reward_variance",
        "unique_rollout_rate",
    }
)


@dataclass(frozen=True, slots=True)
class ResearchMetricEventV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-metric.v1"

    schema: str
    tenant_id: str
    run_id: str
    stage_id: str
    attempt_id: str
    sequence: int
    metric: str
    value: float
    unit: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchMetricEventV1:
        if set(value) != {
            "schema",
            "tenant_id",
            "run_id",
            "stage_id",
            "attempt_id",
            "sequence",
            "metric",
            "value",
            "unit",
        }:
            raise ResearchTrainingContractError("research_metric_fields_invalid")
        sequence = value.get("sequence")
        metric_value = value.get("value")
        metric = str(value.get("metric") or "").strip()
        if (
            value.get("schema") != cls.SCHEMA
            or metric not in RESEARCH_METRICS
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or not 0 <= sequence <= 10**9
            or not isinstance(metric_value, (int, float))
            or isinstance(metric_value, bool)
            or not math.isfinite(float(metric_value))
        ):
            raise ResearchTrainingContractError("research_metric_invalid")
        return cls(
            schema=cls.SCHEMA,
            tenant_id=require_id(value.get("tenant_id"), "tenant_id"),
            run_id=require_id(value.get("run_id"), "run_id"),
            stage_id=require_id(value.get("stage_id"), "stage_id"),
            attempt_id=require_id(value.get("attempt_id"), "attempt_id"),
            sequence=sequence,
            metric=metric,
            value=float(metric_value),
            unit=require_id(value.get("unit"), "metric_unit"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


__all__ = ["RESEARCH_METRICS", "ResearchMetricEventV1"]
