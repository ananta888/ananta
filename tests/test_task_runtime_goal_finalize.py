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
