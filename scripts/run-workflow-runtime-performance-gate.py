#!/usr/bin/env python3
"""Collect projection samples and evaluate Compose workflow-runtime P95 gates."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.workflow_control_read_model_projector import (  # noqa: E402
    WorkflowControlReadModelProjector,
)
from agent.services.workflow_runtime._serialization import sha256_json  # noqa: E402
from agent.services.workflow_runtime.events import InMemoryEventStore  # noqa: E402
from agent.services.workflow_runtime_performance_gate import (  # noqa: E402
    COMPOSE_REFERENCE_PROFILE,
    WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA,
    WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA,
    ComposeWorkflowRuntimePerformanceEvidence,
)
from agent.services.workflow_runtime_read_model_service import (  # noqa: E402
    InMemoryWorkflowRuntimeReadModelRepository,
    WorkflowRuntimeReadModelService,
)


@dataclass(frozen=True)
class _ProjectionBinding:
    tenant_id: str
    workflow_id: str
    run_id: str
    request: Any


def collect_projection_samples(count: int) -> dict[str, Any]:
    if not 10 <= int(count) <= 100:
        raise ValueError("projection sample count must be between 10 and 100")
    read_models = WorkflowRuntimeReadModelService(InMemoryWorkflowRuntimeReadModelRepository())
    projector = WorkflowControlReadModelProjector(
        read_models,
        event_store=InMemoryEventStore(),
    )
    samples: list[float] = []
    for index in range(int(count) + 3):
        run_id = f"compose-projection-{index}"
        binding = _ProjectionBinding(
            tenant_id="compose-performance-tenant",
            workflow_id=f"compose-performance-workflow-{index}",
            run_id=run_id,
            request=SimpleNamespace(correlation_id=f"compose-correlation-{index}"),
        )
        started_ns = time.perf_counter_ns()
        record = projector.project(
            binding=binding,
            status={
                "status": "running",
                "task_id": f"compose-task-{index}",
                "updated_at": 1_700_000_000.0 + index,
            },
            runtime="temporal",
            mode="durable",
            capabilities=("authorization", "audit", "checkpoint", "resume"),
        )
        elapsed_ms = round(
            max(0, time.perf_counter_ns() - started_ns) / 1_000_000.0,
            6,
        )
        if record.run_id != run_id or record.runtime != "temporal":
            raise RuntimeError("projection performance binding mismatch")
        if index >= 3:
            samples.append(elapsed_ms)
    return {
        "schema": WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA,
        "component": "hub_event_projection",
        "runtime_id": "temporal",
        "reference_profile": COMPOSE_REFERENCE_PROFILE,
        "sample_count": len(samples),
        "metrics": {"event_projection": {"samples_ms": samples}},
    }


def evaluate_components(
    paths: Sequence[str | Path],
    *,
    source_revision: str,
    generated_at: float,
) -> ComposeWorkflowRuntimePerformanceEvidence:
    metrics: dict[str, object] = {}
    runtime_id = ""
    for path in paths:
        component = _read_component(Path(path))
        if component.get("reference_profile") != COMPOSE_REFERENCE_PROFILE:
            raise ValueError("workflow_runtime_performance_profile_unsupported")
        component_runtime = str(component.get("runtime_id") or "").strip()
        if runtime_id and component_runtime != runtime_id:
            raise ValueError("workflow_runtime_performance_runtime_mismatch")
        runtime_id = component_runtime
        raw_metrics = component.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError("workflow_runtime_performance_metrics_required")
        for metric, raw in raw_metrics.items():
            if metric in metrics:
                raise ValueError(f"workflow_runtime_performance_metric_duplicate:{metric}")
            if not isinstance(raw, Mapping):
                raise ValueError("workflow_runtime_performance_samples_required")
            metrics[str(metric)] = raw.get("samples_ms")
    return ComposeWorkflowRuntimePerformanceEvidence.build(
        runtime_id=runtime_id,
        source_revision=source_revision,
        generated_at=generated_at,
        samples=metrics,
    )


def _read_component(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("workflow_runtime_performance_component_unavailable") from exc
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and raw.get("schema") == WORKFLOW_RUNTIME_PERFORMANCE_SAMPLES_SCHEMA:
            records.append(raw)
    if len(records) != 1:
        raise ValueError("workflow_runtime_performance_component_record_invalid")
    return records[0]


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    projection = commands.add_parser("sample-projection")
    projection.add_argument("--count", type=int, default=20)
    projection.add_argument("--output", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--component", action="append", required=True)
    evaluate.add_argument("--source-revision", required=True)
    evaluate.add_argument("--generated-at", type=float, default=0.0)
    evaluate.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "sample-projection":
        _write_json(Path(args.output), collect_projection_samples(args.count))
        return 0
    generated_at = float(args.generated_at or time.time())
    try:
        evidence = evaluate_components(
            args.component,
            source_revision=str(args.source_revision),
            generated_at=generated_at,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        blocked = {
            "schema": WORKFLOW_RUNTIME_COMPOSE_PERFORMANCE_SCHEMA,
            "status": "blocked",
            "reason_code": str(exc),
            "source_revision": str(args.source_revision),
            "generated_at": generated_at,
            "reference_profile": COMPOSE_REFERENCE_PROFILE,
            "component_digests": [sha256_json({"path": str(Path(path).name)}) for path in args.component],
        }
        _write_json(Path(args.output), blocked)
        return 1
    _write_json(Path(args.output), evidence.to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
