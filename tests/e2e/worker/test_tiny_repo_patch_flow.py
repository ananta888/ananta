from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from worker.coding.diff_builder import build_unified_diff
from worker.coding.patch_apply import apply_patch_artifact
from worker.core.trace import attach_trace_to_result, build_trace_metadata, stable_hash
from worker.core.verification import build_verification_artifact
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


def test_tiny_repo_patch_flow_produces_plan_patch_test_verify_and_trace(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    calc_path = repo / "calc.py"
    original = calc_path.read_text(encoding="utf-8")
    calc_path.write_text(original.replace("return a - b", "return a + b"), encoding="utf-8")
    patch_artifact = build_unified_diff(repository_root=repo).as_artifact(
        task_id="AW-T28",
        capability_id="worker.patch.propose",
        risk_classification="high",
    )
    _run(["git", "checkout", "--", "calc.py"], cwd=repo)

    # Ensure no main working-tree mutation before approval/apply.
    assert calc_path.read_text(encoding="utf-8") == original

    approval = {
        "status": "approved",
        "task_id": "AW-T28",
        "capability_id": "worker.patch.apply",
        "context_hash": "ctx-28",
        "patch_hash": patch_artifact["patch_hash"],
    }
    apply_result = apply_patch_artifact(
        repository_root=repo,
        patch_artifact=patch_artifact,
        task_id="AW-T28",
        capability_id="worker.patch.apply",
        context_hash="ctx-28",
        policy_decision="approval_required",
        approval=approval,
    )
    assert apply_result["status"] == "applied"

    command_plan = build_command_plan_artifact(
        task_id="AW-T28",
        capability_id="worker.command.plan",
        command="python3 -m unittest discover -s tests -p 'test_*.py'",
        explanation="Run tiny repo tests",
        expected_effects=["Collects pytest result for verification."],
        policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
    )
    test_result = execute_command_plan(
        repository_root=repo,
        command_plan_artifact=command_plan,
        task_id="AW-T28",
        capability_id="worker.command.execute",
        context_hash="ctx-28",
        shell_policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
        hub_policy_decision="allow",
    )
    verification = build_verification_artifact(task_id="AW-T28", test_results=[test_result], patch_artifact=patch_artifact)
    assert verification["status"] == "passed"

    trace_metadata = build_trace_metadata(
        trace_id="tr-28",
        task_id="AW-T28",
        capability_id="worker.command.execute",
        context_hash="ctx-28",
        policy_decision_ref={"decision_id": "d-28", "decision": "allow", "policy_version": "v1"},
    )
    result = attach_trace_to_result(
        result={
            "schema": "worker_execution_result.v1",
            "task_id": "AW-T28",
            "trace_id": "tr-28",
            "status": "completed",
            "artifacts": [
                {"artifact_type": "patch_artifact", "artifact_ref": f"patch:{patch_artifact['patch_hash']}"},
                {"artifact_type": "test_result_artifact", "artifact_ref": f"command:{command_plan['command_hash']}"},
                {"artifact_type": "verification_artifact", "artifact_ref": "verify:AW-T28"},
            ],
        },
        trace_metadata=trace_metadata,
        mode="command_execute",
    )
    assert result["context_hash"] == "ctx-28"
    assert result["capability_id"] == "worker.command.execute"
    assert command_plan["command_hash"] == stable_hash(command_plan["command"])
