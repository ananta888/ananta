"""Side-effect-free Temporal Compose start/signal latency sampler."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import timedelta
from typing import Any

from temporalio.common import WorkflowIDReusePolicy

from ananta_contracts.temporal_workflow import ProbeRequest
from worker.temporal.smoke import _client, _wait_for_recovery_state
from worker.temporal.workflows import RECOVERY_PROBE_WORKFLOW_TYPE

PERFORMANCE_SAMPLES_SCHEMA = "ananta.workflow_runtime_performance_samples.v1"
REFERENCE_PROFILE = "docker-compose-temporal-reference-v1"


def _sample_count() -> int:
    try:
        value = int(os.getenv("ANANTA_TEMPORAL_PERFORMANCE_SAMPLES", "20"))
    except ValueError as exc:
        raise ValueError(
            "ANANTA_TEMPORAL_PERFORMANCE_SAMPLES must be an integer"
        ) from exc
    if not 10 <= value <= 100:
        raise ValueError(
            "ANANTA_TEMPORAL_PERFORMANCE_SAMPLES must be between 10 and 100"
        )
    return value


async def collect_performance_samples() -> dict[str, Any]:
    client, namespace, task_queue, timeout = await _client()
    start_samples: list[float] = []
    signal_samples: list[float] = []
    run_ids: list[str] = []

    for index in range(_sample_count()):
        workflow_id = f"ananta-compose-perf-{index}-{uuid.uuid4().hex}"
        request_id = f"{workflow_id}-request"
        started_ns = time.perf_counter_ns()
        handle = await asyncio.wait_for(
            client.start_workflow(
                RECOVERY_PROBE_WORKFLOW_TYPE,
                ProbeRequest(request_id=request_id, value="compose-performance"),
                id=workflow_id,
                task_queue=task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(minutes=5),
            ),
            timeout=timeout,
        )
        state = await _wait_for_recovery_state(
            handle,
            expected_status="waiting",
            timeout=timeout,
        )
        start_samples.append(_elapsed_ms(started_ns))
        if state.get("request_id") != request_id:
            raise RuntimeError("Temporal performance start binding mismatch")
        run_ids.append(str(state.get("run_id") or ""))

        signalled_ns = time.perf_counter_ns()
        await asyncio.wait_for(handle.signal("release", request_id), timeout=timeout)
        result = await asyncio.wait_for(handle.result(), timeout=timeout)
        signal_samples.append(_elapsed_ms(signalled_ns))
        if (
            not isinstance(result, dict)
            or result.get("status") != "completed"
            or result.get("request_id") != request_id
            or result.get("run_id") != state.get("run_id")
        ):
            raise RuntimeError("Temporal performance signal binding mismatch")

    return {
        "schema": PERFORMANCE_SAMPLES_SCHEMA,
        "component": "temporal_start_signal",
        "runtime_id": "temporal",
        "reference_profile": REFERENCE_PROFILE,
        "namespace": namespace,
        "task_queue": task_queue,
        "workflow_type": RECOVERY_PROBE_WORKFLOW_TYPE,
        "sample_count": len(start_samples),
        "run_id_digests": [_opaque_run_id(value) for value in run_ids],
        "metrics": {
            "start": {"samples_ms": start_samples},
            "signal": {"samples_ms": signal_samples},
        },
    }


def _elapsed_ms(started_ns: int) -> float:
    return round(max(0, time.perf_counter_ns() - started_ns) / 1_000_000.0, 6)


def _opaque_run_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def main() -> None:
    print(
        json.dumps(
            asyncio.run(collect_performance_samples()),
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()


__all__ = ["collect_performance_samples", "main"]
