"""APH-T005: real pytest-backed verification in full-flow style.

Uses generated files in an isolated workspace, runs real pytest, and feeds
verification evidence into TaskCompletionPolicyService.
"""
from __future__ import annotations

import subprocess
import os
from pathlib import Path

from agent.services.task_completion_policy_service import get_task_completion_policy_service


def _write_project(workspace: Path, *, failing: bool) -> None:
    (workspace / "tests").mkdir(parents=True, exist_ok=True)
    (workspace / "app.py").write_text(
        "def fibonacci(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n",
        encoding="utf-8",
    )
    expected = 54 if failing else 55
    (workspace / "tests" / "test_app.py").write_text(
        "from app import fibonacci\n\n"
        "def test_fibonacci_ten():\n"
        f"    assert fibonacci(10) == {expected}\n",
        encoding="utf-8",
    )


def _verify_with_pytest(workspace: Path) -> bool:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        ["pytest", "-q", "tests/test_app.py"],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _collection(verified: bool) -> dict:
    status = "verified" if verified else "unverified"
    return {
        "manifest_valid": True,
        "synthesized": True,
        "collection_method": "synthesized_from_diff",
        "errors": [],
        "warnings": [],
        "artifacts": [
            {"artifact_id": "a1", "relative_path": "app.py", "_exists": True, "required": True, "verification_status": status},
            {"artifact_id": "a2", "relative_path": "tests/test_app.py", "_exists": True, "required": True, "verification_status": status},
        ],
    }


def test_real_pytest_success_allows_completed(tmp_path: Path):
    workspace = tmp_path / "verify-success"
    _write_project(workspace, failing=False)
    verified = _verify_with_pytest(workspace)

    decision = get_task_completion_policy_service().evaluate(
        task_id="verify-success",
        goal_id="verify-success",
        collection_result=_collection(verified),
        exit_code=0,
        expected_paths=["app.py", "tests/test_app.py"],
        verification_required=True,
        allow_synthesized_manifest=True,
    )

    assert verified is True
    assert decision.decision == "completed"


def test_real_pytest_failure_blocks_completed(tmp_path: Path):
    workspace = tmp_path / "verify-fail"
    _write_project(workspace, failing=True)
    verified = _verify_with_pytest(workspace)

    decision = get_task_completion_policy_service().evaluate(
        task_id="verify-fail",
        goal_id="verify-fail",
        collection_result=_collection(verified),
        exit_code=0,
        expected_paths=["app.py", "tests/test_app.py"],
        verification_required=True,
        allow_synthesized_manifest=True,
    )

    assert verified is False
    assert decision.decision != "completed"
