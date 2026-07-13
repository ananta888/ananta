"""One-shot Compose smoke gate for Temporal registration and connectivity."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy

from ananta_contracts.temporal_workflow import ProbeRequest
from worker.temporal.workflows import (
    PROBE_WORKFLOW_TYPE,
    RECOVERY_PROBE_WORKFLOW_TYPE,
)


def _timeout_seconds() -> int:
    try:
        value = int(os.getenv("ANANTA_TEMPORAL_SMOKE_TIMEOUT_SECONDS", "60"))
    except ValueError as exc:
        raise ValueError("ANANTA_TEMPORAL_SMOKE_TIMEOUT_SECONDS must be an integer") from exc
    if not 5 <= value <= 600:
        raise ValueError("ANANTA_TEMPORAL_SMOKE_TIMEOUT_SECONDS must be between 5 and 600")
    return value


def _recovery_probe_id() -> str:
    value = str(os.getenv("ANANTA_TEMPORAL_RECOVERY_PROBE_ID") or "ananta-compose-recovery-probe-v1").strip()
    if not value or len(value) > 256 or any(character.isspace() for character in value):
        raise ValueError("ANANTA_TEMPORAL_RECOVERY_PROBE_ID is invalid")
    return value


async def _client() -> tuple[Client, str, str, int]:
    address = str(os.getenv("ANANTA_TEMPORAL_ADDRESS") or "temporal:7233").strip()
    namespace = str(os.getenv("ANANTA_TEMPORAL_NAMESPACE") or "default").strip()
    task_queue = str(os.getenv("ANANTA_TEMPORAL_TASK_QUEUE") or "ananta-workflows-v1").strip()
    if not address or not namespace or not task_queue:
        raise ValueError("Temporal smoke binding is incomplete")
    timeout = _timeout_seconds()
    client = await asyncio.wait_for(
        Client.connect(address, namespace=namespace),
        timeout=timeout,
    )
    return client, namespace, task_queue, timeout


async def _wait_for_recovery_state(
    handle: Any,
    *,
    expected_status: str,
    timeout: int,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    last_state: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        try:
            state = await handle.query("recovery_state")
        except Exception:  # noqa: BLE001 - bounded connectivity probe
            await asyncio.sleep(0.2)
            continue
        if isinstance(state, dict):
            last_state = dict(state)
            if last_state.get("status") == expected_status:
                return last_state
        await asyncio.sleep(0.2)
    raise TimeoutError(f"recovery probe did not reach {expected_status}: {last_state.get('status', 'unknown')}")


async def run_smoke() -> dict[str, str]:
    client, namespace, task_queue, timeout = await _client()
    request_id = f"compose-probe-{uuid.uuid4().hex}"
    result = await asyncio.wait_for(
        client.execute_workflow(
            PROBE_WORKFLOW_TYPE,
            ProbeRequest(request_id=request_id, value="compose-smoke"),
            id=request_id,
            task_queue=task_queue,
            execution_timeout=timedelta(seconds=timeout),
        ),
        timeout=timeout,
    )
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise RuntimeError("Temporal probe returned an invalid result")
    if result.get("request_id") != request_id or result.get("value") != "compose-smoke":
        raise RuntimeError("Temporal probe binding mismatch")
    return {
        "schema": "ananta.temporal-compose-smoke.v1",
        "status": "passed",
        "namespace": namespace,
        "task_queue": task_queue,
        "workflow_type": PROBE_WORKFLOW_TYPE,
    }


async def start_recovery_probe() -> dict[str, str | int]:
    client, namespace, task_queue, timeout = await _client()
    workflow_id = _recovery_probe_id()
    request_id = f"{workflow_id}-request"
    handle = await asyncio.wait_for(
        client.start_workflow(
            RECOVERY_PROBE_WORKFLOW_TYPE,
            ProbeRequest(request_id=request_id, value="compose-recovery"),
            id=workflow_id,
            task_queue=task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            execution_timeout=timedelta(minutes=10),
        ),
        timeout=timeout,
    )
    state = await _wait_for_recovery_state(
        handle,
        expected_status="waiting",
        timeout=timeout,
    )
    if state.get("request_id") != request_id or state.get("value") != "compose-recovery":
        raise RuntimeError("Temporal recovery probe start binding mismatch")
    return {
        "schema": "ananta.temporal-compose-recovery-evidence.v1",
        "status": "waiting_before_restart",
        "namespace": namespace,
        "task_queue": task_queue,
        "workflow_type": RECOVERY_PROBE_WORKFLOW_TYPE,
        "workflow_id": workflow_id,
        "run_id": str(state.get("run_id") or ""),
        "request_id": request_id,
        "rejected_release_count": int(state.get("rejected_release_count") or 0),
    }


async def complete_recovery_probe() -> dict[str, str | int]:
    client, namespace, task_queue, timeout = await _client()
    workflow_id = _recovery_probe_id()
    request_id = f"{workflow_id}-request"
    handle = client.get_workflow_handle(workflow_id)
    state = await _wait_for_recovery_state(
        handle,
        expected_status="waiting",
        timeout=timeout,
    )
    if state.get("request_id") != request_id or state.get("value") != "compose-recovery":
        raise RuntimeError("Temporal recovery probe persisted binding mismatch")
    await asyncio.wait_for(handle.signal("release", request_id), timeout=timeout)
    result = await asyncio.wait_for(handle.result(), timeout=timeout)
    if (
        not isinstance(result, dict)
        or result.get("status") != "completed"
        or result.get("request_id") != request_id
        or result.get("run_id") != state.get("run_id")
        or int(result.get("rejected_release_count") or 0) != 0
    ):
        raise RuntimeError("Temporal recovery probe completion mismatch")
    return {
        "schema": "ananta.temporal-compose-recovery-evidence.v1",
        "status": "completed_after_restart",
        "namespace": namespace,
        "task_queue": task_queue,
        "workflow_type": RECOVERY_PROBE_WORKFLOW_TYPE,
        "workflow_id": workflow_id,
        "run_id": str(result.get("run_id") or ""),
        "request_id": request_id,
        "rejected_release_count": int(result.get("rejected_release_count") or 0),
    }


def main() -> None:
    mode = str(os.getenv("ANANTA_TEMPORAL_SMOKE_MODE") or "probe").strip()
    if mode == "probe":
        result = asyncio.run(run_smoke())
    elif mode == "start-recovery":
        result = asyncio.run(start_recovery_probe())
    elif mode == "complete-recovery":
        result = asyncio.run(complete_recovery_probe())
    else:
        raise ValueError("ANANTA_TEMPORAL_SMOKE_MODE is unsupported")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()


__all__ = [
    "complete_recovery_probe",
    "main",
    "run_smoke",
    "start_recovery_probe",
]
