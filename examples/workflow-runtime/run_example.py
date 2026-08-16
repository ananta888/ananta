"""Executable runtime-neutral example runner with no pytest dependency."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from offline_runtime_drills import run_langgraph_drill, run_native_drill
from runtime_example_support import (
    EVIDENCE_FILE,
    EXAMPLE_SCHEMA,
    EXAMPLE_TASK_QUEUE,
    HUB_EVENTS_FILE,
    PLAN_REF,
    PREPARED_FILE,
    assert_evidence_contract,
    build_temporal_input,
    evidence_boundary,
    load_plan,
    signed_temporal_command,
    temporal_workflow_id,
)
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy

ANANTA_WORKFLOW_TYPE = "AnantaWorkflow"


def _evidence_dir() -> Path:
    return Path(os.getenv("ANANTA_EXAMPLE_EVIDENCE_DIR", "/evidence"))


async def _client() -> Client:
    address = str(os.getenv("ANANTA_TEMPORAL_ADDRESS") or "temporal:7233")
    namespace = str(os.getenv("ANANTA_TEMPORAL_NAMESPACE") or "default")
    deadline = asyncio.get_running_loop().time() + 60
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            return await Client.connect(
                address,
                namespace=namespace,
                identity="ananta-workflow-runtime-example-runner",
            )
        except Exception as exc:  # noqa: BLE001 - bounded container startup retry
            last_error = exc
            await asyncio.sleep(0.5)
    raise RuntimeError("example_temporal_connection_timeout") from last_error


async def _wait_for(
    handle: Any,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    reason: str,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 60
    last: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        try:
            raw = await handle.query("status")
            if isinstance(raw, dict):
                last = dict(raw)
                if predicate(last):
                    return last
        except Exception:  # noqa: BLE001 - worker may be starting/replaying
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError(f"{reason}:{last.get('status', 'unknown')}")


def _is_temporal_not_ready(error: Exception) -> bool:
    message = f"{error.__class__.__name__} {error}".lower()
    return "workflownotready" in message or "not ready" in message and "workflow" in message


async def _approve_update_with_retry(handle: Any, command: dict[str, Any], label: str) -> Any:
    deadline = asyncio.get_running_loop().time() + 15
    retry_delay = 0.25
    last: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            return await handle.execute_update("command", command)
        except Exception as exc:  # noqa: BLE001 - workflow readiness is transient in CI
            if not _is_temporal_not_ready(exc):
                raise
            last = exc
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 1.0)
    raise RuntimeError(f"{label}:workflow_not_ready_retry_exhausted") from last


async def _start(client: Client, workflow_input) -> Any:
    return await client.start_workflow(
        ANANTA_WORKFLOW_TYPE,
        workflow_input.to_dict(),
        id=workflow_input.workflow_id,
        task_queue=EXAMPLE_TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )


async def _approve(handle: Any, workflow_input, status: dict[str, Any], command_id: str):
    command = signed_temporal_command(
        workflow_input,
        status,
        command_type="approve",
        command_id=command_id,
        now=time.time() - 1,
    )
    result = await _approve_update_with_retry(handle, command, f"example_temporal_approval_{command_id}")
    if not isinstance(result, dict) or result.get("accepted") is not True:
        raise RuntimeError("example_temporal_approval_rejected")
    return result


async def _history_count(handle: Any) -> int:
    history = await handle.fetch_history()
    return len(history.events)


async def _run_completed_scenario(client: Client, plan, scenario: str) -> dict[str, Any]:
    workflow_input = build_temporal_input(plan, scenario=scenario)
    handle = await _start(client, workflow_input)
    waiting = await _wait_for(
        handle,
        lambda value: value.get("status") == "waiting_approval",
        reason=f"example_temporal_{scenario}_approval_timeout",
    )
    approval = await _approve(
        handle,
        workflow_input,
        waiting,
        f"example-{scenario}-approval-v1",
    )
    result = await handle.result()
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise RuntimeError(f"example_temporal_{scenario}_not_completed")
    return {
        "status": "observed",
        "terminal_status": result["status"],
        "completed_steps": list(result.get("completed_step_ids") or ()),
        "retry_budget_remaining": int(result.get("retry_budget_remaining") or 0),
        "approval_revision": int(approval["revision"]),
        "history_event_count": await _history_count(handle),
    }


async def _run_cancel_scenario(client: Client, plan) -> dict[str, Any]:
    workflow_input = build_temporal_input(plan, scenario="cancel")
    handle = await _start(client, workflow_input)
    running = await _wait_for(
        handle,
        lambda value: value.get("status") == "running" and "draft" in set(value.get("active_step_ids") or ()),
        reason="example_temporal_cancel_activity_timeout",
    )
    command = signed_temporal_command(
        workflow_input,
        running,
        command_type="cancel",
        command_id="example-temporal-cancel-v1",
        payload={"reason": "example_operator_cancelled"},
        now=time.time() - 1,
    )
    accepted = await _approve_update_with_retry(handle, command, "example_temporal_cancel")
    result = await handle.result()
    if (
        not isinstance(accepted, dict)
        or accepted.get("accepted") is not True
        or not isinstance(result, dict)
        or result.get("status") != "cancelled"
    ):
        raise RuntimeError("example_temporal_cancel_not_propagated")
    return {
        "status": "observed",
        "terminal_status": result["status"],
        "reason_code": result.get("reason_code"),
        "activity_was_running": True,
        "history_event_count": await _history_count(handle),
    }


async def _prepare_temporal(plan) -> dict[str, Any]:
    client = await _client()
    failure = await _run_completed_scenario(client, plan, "failure")
    failure.update(
        {
            "reason_code": "example_fake_transient_failure",
            "retry_observation": "hub_event_log",
        }
    )
    approval = await _run_completed_scenario(client, plan, "approval")
    cancel = await _run_cancel_scenario(client, plan)

    crash_input = build_temporal_input(plan, scenario="crash")
    crash_handle = await _start(client, crash_input)
    waiting = await _wait_for(
        crash_handle,
        lambda value: value.get("status") == "waiting_approval",
        reason="example_temporal_crash_checkpoint_timeout",
    )
    crash = {
        "status": "waiting_before_worker_sigkill",
        "checkpoint_ref": waiting["checkpoint_ref"],
        "revision": int(waiting["revision"]),
        "completed_steps": list(waiting.get("completed_step_ids") or ()),
        "history_event_count": await _history_count(crash_handle),
        "required_host_action": "SIGKILL workflow-runtime-example-temporal-worker",
    }
    return {
        "runtime_path": "worker.temporal.workflows.AnantaWorkflow",
        "activity_gateway_path": "worker.temporal.hub_gateway.HttpHubTaskGateway",
        "plan_hash": plan.plan_hash,
        "classification": "real_temporal_with_example_hub_port",
        "durable": True,
        "durable_server_path": True,
        "scenarios": {
            "failure": failure,
            "approval": approval,
            "cancel": cancel,
            "crash": crash,
        },
    }


def _base_evidence(plan) -> dict[str, Any]:
    return {
        "schema": EXAMPLE_SCHEMA,
        "classification": "example_only",
        "production_release_gate": False,
        "status": "prepared",
        "plan": {"ref": PLAN_REF, "hash": plan.plan_hash},
        "boundaries": evidence_boundary(),
        "security": {
            "network_provider_calls": False,
            "production_credentials": False,
            "worker_to_worker_flows": False,
            "hub_task_owner": "workflow-runtime-example-hub",
        },
        "runtimes": {},
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_hub_events(directory: Path) -> list[dict[str, Any]]:
    path = directory / HUB_EVENTS_FILE
    if not path.exists():
        raise RuntimeError("example_hub_event_evidence_missing")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError("example_hub_event_evidence_invalid")
        events.append(value)
    return events


async def prepare() -> dict[str, Any]:
    plan = load_plan()
    evidence = _base_evidence(plan)
    evidence["runtimes"] = {
        "native": run_native_drill(plan),
        "langgraph": run_langgraph_drill(plan),
        "temporal": await _prepare_temporal(plan),
    }
    assert_evidence_contract(evidence, final=False)
    _write_json(_evidence_dir() / PREPARED_FILE, evidence)
    return evidence


async def resume() -> dict[str, Any]:
    directory = _evidence_dir()
    prepared_path = directory / PREPARED_FILE
    if not prepared_path.exists():
        raise RuntimeError("example_prepared_evidence_missing")
    evidence = json.loads(prepared_path.read_text(encoding="utf-8"))
    assert_evidence_contract(evidence, final=False)
    plan = load_plan()
    if evidence["plan"]["hash"] != plan.plan_hash:
        raise RuntimeError("example_plan_changed_between_prepare_and_resume")

    client = await _client()
    workflow_input = build_temporal_input(plan, scenario="crash")
    handle = client.get_workflow_handle(temporal_workflow_id("crash"))
    waiting = await _wait_for(
        handle,
        lambda value: value.get("status") == "waiting_approval",
        reason="example_temporal_resume_replay_timeout",
    )
    before = evidence["runtimes"]["temporal"]["scenarios"]["crash"]
    if waiting.get("checkpoint_ref") != before.get("checkpoint_ref") or int(waiting.get("revision") or -1) != int(
        before.get("revision") or -2
    ):
        raise RuntimeError("example_temporal_checkpoint_changed_during_restart")
    await _approve(
        handle,
        workflow_input,
        waiting,
        "example-crash-resume-approval-v1",
    )
    result = await handle.result()
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise RuntimeError("example_temporal_resume_not_completed")
    after_history = await _history_count(handle)
    if after_history <= int(before["history_event_count"]):
        raise RuntimeError("example_temporal_history_did_not_advance")

    events = _read_hub_events(directory)
    event_names = [str(value.get("event") or "") for value in events]
    if "task_submission_failed" not in event_names or "task_cancelled" not in event_names:
        raise RuntimeError("example_temporal_hub_events_incomplete")
    temporal = evidence["runtimes"]["temporal"]
    temporal["scenarios"]["resume"] = {
        "status": "completed_after_worker_sigkill",
        "terminal_status": result["status"],
        "checkpoint_preserved": True,
        "completed_steps": list(result.get("completed_step_ids") or ()),
        "history_event_count_before": int(before["history_event_count"]),
        "history_event_count_after": after_history,
    }
    temporal["hub_event_summary"] = {name: event_names.count(name) for name in sorted(set(event_names))}
    temporal["scenarios"]["crash"]["status"] = "worker_sigkill_completed"
    evidence["status"] = "passed_example"
    assert_evidence_contract(evidence, final=True)
    _write_json(directory / EVIDENCE_FILE, evidence)
    return evidence


def validate_evidence() -> dict[str, Any]:
    path = _evidence_dir() / EVIDENCE_FILE
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert_evidence_contract(evidence, final=True)
    return evidence


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("prepare", "resume", "validate-evidence"),
        default=os.getenv("ANANTA_EXAMPLE_MODE", "prepare"),
    )
    return parser.parse_args()


def main() -> None:
    arguments = _arguments()
    if arguments.mode == "prepare":
        evidence = asyncio.run(prepare())
    elif arguments.mode == "resume":
        evidence = asyncio.run(resume())
    else:
        evidence = validate_evidence()
    json.dump(evidence, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()


__all__ = ["main", "prepare", "resume", "validate_evidence"]
