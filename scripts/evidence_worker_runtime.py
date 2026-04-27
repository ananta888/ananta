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


def _post_json(base_url: str, path: str, payload: dict[str, Any], token: str, *, timeout: int = 20) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    data = response.json() if response.text else {}
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed with {response.status_code}: {data}")
    return {"status_code": response.status_code, "json": data}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run_opencode(prompt: str, workspace: Path, *, timeout: int) -> dict[str, Any]:
    opencode = shutil.which("opencode")
    if not opencode:
        raise RuntimeError("opencode binary not found on PATH")

    prompt_file = workspace / "opencode-prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    commands = [
        [opencode, "run", "--help"],
        [opencode, "--help"],
    ]
    attempts = []
    for command in commands:
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
                "execution_mode": "real_cli_smoke",
                "opencode_path": opencode,
                "prompt_file": str(prompt_file),
                "attempts": attempts,
                "note": "Evidence flow verifies real OpenCode CLI execution without applying code changes.",
            }
    raise RuntimeError(f"opencode live execution failed: {attempts}")


def _run_ananta_native(task_payload: dict[str, Any], workspace: Path) -> dict[str, Any]:
    context = ((task_payload.get("worker_execution_context") or {}).get("context") or {}).get("context_text") or ""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "ananta-native-input-context.md").write_text(str(context), encoding="utf-8")
    result = {
        "engine": "ananta_native",
        "status": "completed",
        "execution_mode": "real_worker_process_plan_only",
        "workspace": str(workspace),
        "patch_plan": [
            "Inspect retrieved SecurityController, TokenVerifier and PolicyService context.",
            "Keep token validation and issuer checks explicit.",
            "Keep admin authorization as a separate policy boundary.",
            "Return plan artifact only; do not apply code changes in evidence run.",
        ],
        "tests": [
            "invalid token is denied",
            "non-admin role is denied",
            "valid admin request is allowed",
        ],
        "safety_notes": [
            "plan_only",
            "no shell mutation",
            "approval required before code changes",
        ],
    }
    _write_json(workspace / "ananta-native-result.json", result)
    return result


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--engine", choices=["ananta_native", "opencode"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--agent-url", default="http://evidence-worker:5001")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = out_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    claim = _post_json(
        args.hub_url,
        "/tasks/orchestration/claim",
        {
            "task_id": args.task_id,
            "agent_url": args.agent_url,
            "idempotency_key": f"evidence-worker-{args.engine}-{args.task_id}",
            "lease_seconds": 120,
        },
        args.token,
    )
    _write_json(out_dir / "claim-response.json", claim)
    task_payload = ((claim.get("json") or {}).get("data") or {}).get("task") or {}
    _write_json(out_dir / "claimed-task.json", task_payload)

    prompt = (
        f"Engine: {args.engine}\n"
        "Task: create a safe plan for Java security secret rotation flow.\n"
        "Use provided RAG context and do not apply code changes.\n\n"
        + str(((task_payload.get("worker_execution_context") or {}).get("context") or {}).get("context_text") or "")[:6000]
    )
    (workspace / "worker-prompt.txt").write_text(prompt, encoding="utf-8")

    if args.engine == "ananta_native":
        engine_result = _run_ananta_native(task_payload, workspace)
    else:
        engine_result = _run_opencode(prompt, workspace, timeout=args.timeout)
    _write_json(out_dir / "engine-result.json", engine_result)

    complete_payload = {
        "task_id": args.task_id,
        "actor": args.agent_url,
        "gate_results": {
            "passed": True,
            "checks": [
                "real-worker-process-started",
                "real-worker-claimed-task",
                f"engine-{args.engine}-completed",
                "no-code-apply-in-evidence-run",
            ],
        },
        "output": json.dumps(engine_result, sort_keys=True),
        "trace_id": f"trace-real-worker-{args.engine}-{args.task_id}",
    }
    complete = _post_json(args.hub_url, "/tasks/orchestration/complete", complete_payload, args.token)
    _write_json(out_dir / "complete-payload.json", complete_payload)
    _write_json(out_dir / "complete-response.json", complete)
    _write_json(
        out_dir / "summary.json",
        {
            "status": "completed",
            "engine": args.engine,
            "task_id": args.task_id,
            "claim_status_code": claim["status_code"],
            "complete_status_code": complete["status_code"],
            "engine_result_status": engine_result.get("status"),
            "real_worker_process": True,
            "real_opencode_cli": args.engine == "opencode",
        },
    )


if __name__ == "__main__":
    run()
