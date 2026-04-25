from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from worker.core.degraded import build_degraded_state
from worker.core.trace import build_trace_metadata
from worker.shell.command_executor import execute_command_plan
from worker.shell.command_planner import build_command_plan_artifact


def _run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, text=True, capture_output=True)


def _prepare_repo(tmp_path: Path) -> Path:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "tiny_patch_repo"
    repo = tmp_path / "repo"
    shutil.copytree(fixture, repo)
    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "worker-e2e@example.local"], cwd=repo)
    _run(["git", "config", "user.name", "worker-e2e"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_denied_command_is_blocked_before_execution_and_traced(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    marker = repo / "marker.txt"
    marker.write_text("safe\n", encoding="utf-8")
    plan = build_command_plan_artifact(
        task_id="AW-T30",
        capability_id="worker.command.plan",
        command="rm -rf /",
        explanation="dangerous request",
        expected_effects=["Should be denied."],
        policy={"allowlist": ["echo"], "approval_required_commands": [], "denylist_tokens": ["rm -rf /"]},
    )
    with pytest.raises(PermissionError, match="policy_denied"):
        execute_command_plan(
            repository_root=repo,
            command_plan_artifact=plan,
            task_id="AW-T30",
            capability_id="worker.command.execute",
            context_hash="ctx-30",
            shell_policy={"allowlist": ["echo"], "approval_required_commands": [], "denylist_tokens": ["rm -rf /"]},
            hub_policy_decision="allow",
        )

    # No side effect in repository files.
    assert marker.read_text(encoding="utf-8") == "safe\n"
    denied = build_degraded_state(state="denied_policy", machine_reason="policy_denied", details={"command": plan["command"]})
    trace = build_trace_metadata(
        trace_id="tr-30",
        task_id="AW-T30",
        capability_id="worker.command.execute",
        context_hash="ctx-30",
        policy_decision_ref={"decision_id": "d-30", "decision": "deny", "policy_version": "v1"},
    )
    assert denied["state"] == "denied_policy"
    assert trace["policy_decision_ref"]["decision"] == "deny"
