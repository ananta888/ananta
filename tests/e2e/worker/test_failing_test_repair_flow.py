from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from worker.coding.diff_builder import build_unified_diff
from worker.coding.patch_apply import apply_patch_artifact
from worker.core.trace import build_trace_metadata
from worker.core.verification import build_verification_artifact
from worker.shell.command_executor import execute_command_plan
from worker.shell.command_planner import build_command_plan_artifact
from worker.shell.command_repair_hints import build_command_repair_hints


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


def test_failing_test_repair_flow_returns_repair_artifacts(tmp_path: Path) -> None:
    repo = _prepare_repo(tmp_path)
    command_plan = build_command_plan_artifact(
        task_id="AW-T29",
        capability_id="worker.command.plan",
        command="python3 -m unittest discover -s tests -p 'test_*.py'",
        explanation="Run tiny repo tests",
        expected_effects=["Detects failing baseline test."],
        policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
    )
    failed_result = execute_command_plan(
        repository_root=repo,
        command_plan_artifact=command_plan,
        task_id="AW-T29",
        capability_id="worker.command.execute",
        context_hash="ctx-29",
        shell_policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
        hub_policy_decision="allow",
    )
    assert failed_result["status"] == "failed"
    hints = build_command_repair_hints(command="python3 -m unittest", exit_code=failed_result["exit_code"], stderr=failed_result["stderr_ref"])
    assert hints

    calc_path = repo / "calc.py"
    original = calc_path.read_text(encoding="utf-8")
    calc_path.write_text(original.replace("return a - b", "return a + b"), encoding="utf-8")
    patch_artifact = build_unified_diff(repository_root=repo).as_artifact(
        task_id="AW-T29",
        capability_id="worker.patch.propose",
        risk_classification="high",
    )
    _run(["git", "checkout", "--", "calc.py"], cwd=repo)
    approval = {
        "status": "approved",
        "task_id": "AW-T29",
        "capability_id": "worker.patch.apply",
        "context_hash": "ctx-29",
        "patch_hash": patch_artifact["patch_hash"],
    }
    apply_patch_artifact(
        repository_root=repo,
        patch_artifact=patch_artifact,
        task_id="AW-T29",
        capability_id="worker.patch.apply",
        context_hash="ctx-29",
        policy_decision="approval_required",
        approval=approval,
    )
    passed_result = execute_command_plan(
        repository_root=repo,
        command_plan_artifact=command_plan,
        task_id="AW-T29",
        capability_id="worker.command.execute",
        context_hash="ctx-29",
        shell_policy={"allowlist": ["python3", "unittest"], "approval_required_commands": [], "denylist_tokens": []},
        hub_policy_decision="allow",
    )
    failed_verification = build_verification_artifact(
        task_id="AW-T29",
        test_results=[failed_result],
        extra_evidence_refs=["repair-hints:AW-T29"],
    )
    assert failed_verification["status"] == "failed"
    verification = build_verification_artifact(task_id="AW-T29", test_results=[passed_result], patch_artifact=patch_artifact)
    assert verification["status"] == "passed"
    trace = build_trace_metadata(
        trace_id="tr-29",
        task_id="AW-T29",
        capability_id="worker.patch.apply",
        context_hash="ctx-29",
        policy_decision_ref={"decision_id": "d-29", "decision": "approval_required", "policy_version": "v1"},
        approval_ref={"approval_id": "a-29", "status": "approved", "task_id": "AW-T29", "capability_id": "worker.patch.apply", "context_hash": "ctx-29"},
    )
    assert trace["approval_ref"]["status"] == "approved"
