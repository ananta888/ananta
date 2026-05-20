from pathlib import Path

from agent.db_models import GoalDB, TaskDB
from agent.repository import goal_repo, task_repo
from agent.services.task_runtime_service import update_local_task_status


def test_goal_finalization_marks_failed_when_output_dir_has_no_files(tmp_path):
    output_dir = tmp_path / "empty-workspace"
    output_dir.mkdir(parents=True, exist_ok=True)
    goal_repo.save(
        GoalDB(
            id="goal-finalize-empty",
            goal="Finalize with empty output",
            summary="s",
            status="planned",
            execution_preferences={"output_dir": str(output_dir)},
        )
    )
    task_repo.save(TaskDB(id="task-finalize-empty", title="t", status="todo", goal_id="goal-finalize-empty"))

    update_local_task_status("task-finalize-empty", "completed", force=True)
    goal = goal_repo.get_by_id("goal-finalize-empty")

    assert goal is not None
    assert goal.status == "failed"
    prefs = dict(goal.execution_preferences or {})
    assert prefs.get("last_status_reason") == "no_workspace_artifact_created"
    assert dict(prefs.get("finalization_diagnostics") or {}).get("workspace_file_count") == 0


def test_goal_finalization_keeps_completed_when_output_dir_has_files(tmp_path):
    output_dir = tmp_path / "workspace-with-file"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "README.md").write_text("# hello\n", encoding="utf-8")
    goal_repo.save(
        GoalDB(
            id="goal-finalize-files",
            goal="Finalize with files",
            summary="s",
            status="planned",
            execution_preferences={"output_dir": str(output_dir)},
        )
    )
    task_repo.save(TaskDB(id="task-finalize-files", title="t", status="todo", goal_id="goal-finalize-files"))

    update_local_task_status("task-finalize-files", "completed", force=True)
    goal = goal_repo.get_by_id("goal-finalize-files")

    assert goal is not None
    assert goal.status == "completed"
    prefs = dict(goal.execution_preferences or {})
    assert dict(prefs.get("finalization_diagnostics") or {}).get("workspace_file_count", 0) >= 1


def test_goal_finalization_fibonacci_requires_source_tests_and_pytest_evidence(tmp_path):
    output_dir = tmp_path / "fib-workspace-missing-evidence"
    (output_dir / "src" / "fibonacci").mkdir(parents=True, exist_ok=True)
    (output_dir / "tests").mkdir(parents=True, exist_ok=True)
    (output_dir / "src" / "fibonacci" / "service.py").write_text("def fib(n): return n\n", encoding="utf-8")
    (output_dir / "tests" / "test_fibonacci_service.py").write_text("def test_smoke(): assert 1 == 1\n", encoding="utf-8")

    goal_repo.save(
        GoalDB(
            id="goal-finalize-fib-missing",
            goal="Create Fibonacci backend project with tests",
            summary="s",
            mode="new_software_project",
            status="planned",
            execution_preferences={"output_dir": str(output_dir)},
        )
    )
    task_repo.save(TaskDB(id="task-finalize-fib-missing", title="t", status="todo", goal_id="goal-finalize-fib-missing"))

    update_local_task_status("task-finalize-fib-missing", "completed", force=True)
    goal = goal_repo.get_by_id("goal-finalize-fib-missing")

    assert goal is not None
    assert goal.status == "failed"
    prefs = dict(goal.execution_preferences or {})
    assert prefs.get("last_status_reason") == "missing_required_fibonacci_artifacts"


def test_goal_finalization_fibonacci_succeeds_with_required_evidence(tmp_path):
    output_dir = tmp_path / "fib-workspace-complete"
    (output_dir / "src" / "fibonacci").mkdir(parents=True, exist_ok=True)
    (output_dir / "tests").mkdir(parents=True, exist_ok=True)
    (output_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (output_dir / "src" / "fibonacci" / "service.py").write_text("def fib(n): return n\n", encoding="utf-8")
    (output_dir / "tests" / "test_fibonacci_service.py").write_text("def test_smoke(): assert 1 == 1\n", encoding="utf-8")
    (output_dir / "artifacts" / "pytest-report.txt").write_text("1 passed\n", encoding="utf-8")

    goal_repo.save(
        GoalDB(
            id="goal-finalize-fib-complete",
            goal="Create Fibonacci backend project with tests",
            summary="s",
            mode="new_software_project",
            status="planned",
            execution_preferences={"output_dir": str(output_dir)},
        )
    )
    task_repo.save(TaskDB(id="task-finalize-fib-complete", title="t", status="todo", goal_id="goal-finalize-fib-complete"))

    update_local_task_status("task-finalize-fib-complete", "completed", force=True)
    goal = goal_repo.get_by_id("goal-finalize-fib-complete")

    assert goal is not None
    assert goal.status == "completed"
