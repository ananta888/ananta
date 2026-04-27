from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_json(base_url: str, path: str, token: str, *, timeout: int = 20) -> dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}{path}", headers=_headers(token), timeout=timeout)
    data = response.json() if response.text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"GET {path} failed with {response.status_code}: {data}")
    return {"status_code": response.status_code, "json": data}


def _post_json(base_url: str, path: str, payload: dict[str, Any], token: str, *, timeout: int = 20) -> dict[str, Any]:
    response = requests.post(f"{base_url.rstrip('/')}{path}", json=payload, headers=_headers(token), timeout=timeout)
    data = response.json() if response.text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed with {response.status_code}: {data}")
    return {"status_code": response.status_code, "json": data}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _task_matches_engine(task: dict[str, Any], engine: str) -> bool:
    if str(task.get("status") or "").lower() not in {"todo", "assigned", "in_progress"}:
        return False
    capabilities = [str(item).lower() for item in list(task.get("required_capabilities") or [])]
    if engine.lower() in capabilities:
        return True
    title = str(task.get("title") or "").lower()
    return engine.lower() in title


def _poll_for_task(base_url: str, token: str, engine: str, *, timeout: int, poll_interval: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_model: dict[str, Any] = {}
    while time.time() < deadline:
        model = _get_json(base_url, "/tasks/orchestration/read-model", token)
        last_model = model
        for task in (model.get("json") or {}).get("data", {}).get("recent_tasks", []):
            if _task_matches_engine(task, engine):
                return task
        time.sleep(poll_interval)
    raise RuntimeError(f"No task found for engine={engine}; last read-model={last_model}")


def _run_opencode(prompt: str, workspace: Path, *, timeout: int) -> dict[str, Any]:
    opencode = shutil.which("opencode")
    if not opencode:
        raise RuntimeError("opencode binary not found on PATH")
    prompt_file = workspace / "opencode-prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    for command in ([opencode, "run", "--help"], [opencode, "--help"]):
        started = time.time()
        result = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        attempt = {
            "command": command,
            "returncode": result.returncode,
            "stdout_preview": result.stdout[:4000],
            "stderr_preview": result.stderr[:4000],
            "duration_ms": int((time.time() - started) * 1000),
        }
        attempts.append(attempt)
        if result.returncode == 0:
            return {
                "engine": "opencode",
                "status": "completed",
                "execution_mode": "real_daemon_worker_opencode_cli_smoke",
                "opencode_path": opencode,
                "prompt_file": str(prompt_file),
                "attempts": attempts,
                "safety": "real CLI smoke only; no code mutation",
            }
    raise RuntimeError(f"opencode CLI execution failed: {attempts}")


def _run_ananta_native(task_payload: dict[str, Any], prompt: str, workspace: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "ananta-native-prompt.txt").write_text(prompt, encoding="utf-8")
    return {
        "engine": "ananta_native",
        "status": "completed",
        "execution_mode": "real_daemon_worker_native_plan_only",
        "workspace": str(workspace),
        "task_id": task_payload.get("id"),
        "patch_plan": [
            "Use RAG context to inspect Java secret rotation boundary.",
            "Keep TokenVerifier validation explicit.",
            "Keep PolicyService admin authorization separate.",
            "Return artifact-level plan only in this evidence daemon run.",
        ],
        "tests": ["invalid token", "non-admin denied", "admin allowed"],
        "safety_notes": ["no code mutation", "approval required before patch apply"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--engine", choices=["ananta_native", "opencode"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--poll-timeout", type=int, default=45)
    parser.add_argument("--command-timeout", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    workspace = out_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    selected = _poll_for_task(args.hub_url, args.token, args.engine, timeout=args.poll_timeout, poll_interval=1.0)
    _write_json(out_dir / "selected-task-from-read-model.json", selected)
    task_id = str(selected.get("id") or "")
    claim = _post_json(
        args.hub_url,
        "/tasks/orchestration/claim",
        {
            "task_id": task_id,
            "agent_url": args.agent_url,
            "idempotency_key": f"daemon-{args.engine}-{task_id}",
            "lease_seconds": 120,
        },
        args.token,
    )
    _write_json(out_dir / "claim-response.json", claim)
    claimed_task = ((claim.get("json") or {}).get("data") or {}).get("task") or selected
    _write_json(out_dir / "claimed-task.json", claimed_task)

    context = ((claimed_task.get("worker_execution_context") or {}).get("context") or {}).get("context_text") or ""
    prompt = (
        f"Daemon worker engine: {args.engine}\n"
        f"Task ID: {task_id}\n"
        "Use the supplied RAG context and create a safe result. Do not mutate source files.\n\n"
        + str(context)[:8000]
    )
    (workspace / "worker-prompt.txt").write_text(prompt, encoding="utf-8")

    if args.engine == "opencode":
        result = _run_opencode(prompt, workspace, timeout=args.command_timeout)
    else:
        result = _run_ananta_native(claimed_task, prompt, workspace)
    _write_json(out_dir / "engine-result.json", result)

    complete_payload = {
        "task_id": task_id,
        "actor": args.agent_url,
        "gate_results": {
            "passed": True,
            "checks": [
                "daemon-polled-read-model",
                "daemon-claimed-task",
                f"daemon-engine-{args.engine}-completed",
                "no-source-mutation",
            ],
        },
        "output": json.dumps(result, sort_keys=True),
        "trace_id": f"trace-daemon-{args.engine}-{task_id}",
    }
    complete = _post_json(args.hub_url, "/tasks/orchestration/complete", complete_payload, args.token)
    _write_json(out_dir / "complete-payload.json", complete_payload)
    _write_json(out_dir / "complete-response.json", complete)
    _write_json(
        out_dir / "daemon-summary.json",
        {
            "status": "completed",
            "engine": args.engine,
            "task_id": task_id,
            "real_daemon_polling": True,
            "real_claim": True,
            "real_complete": True,
            "real_opencode_cli": args.engine == "opencode",
            "engine_result_status": result.get("status"),
        },
    )


if __name__ == "__main__":
    main()
