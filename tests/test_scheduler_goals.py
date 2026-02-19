"""
Additional tests for scheduler goal task functionality.
"""

import time
from unittest.mock import MagicMock, patch
from agent.db_models import ScheduledTaskDB
from agent import scheduler as scheduler_module


class TestSchedulerGoalTasks:
    """Tests for goal-based scheduled tasks."""

    def test_add_goal_task_creates_correct_payload(self, monkeypatch):
        class StubRepo:
            tasks = []

            @staticmethod
            def get_all():
                return StubRepo.tasks

            @staticmethod
            def save(task):
                StubRepo.tasks.append(task)
                return task

            @staticmethod
            def delete(task_id):
                StubRepo.tasks = [t for t in StubRepo.tasks if t.id != task_id]
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        StubRepo.tasks = []
        scheduler = scheduler_module.TaskScheduler()

        task = scheduler.add_goal_task(
            goal="Implement feature X", interval_seconds=3600, context="Using Flask", team_id="team-123"
        )

        assert "goal:Implement feature X" in task.command
        assert "context:Using Flask" in task.command
        assert "team:team-123" in task.command
        assert task.interval_seconds == 3600

    def test_parse_goal_command_extracts_fields(self, monkeypatch):
        class StubRepo:
            @staticmethod
            def get_all():
                return []

            @staticmethod
            def save(task):
                return task

            @staticmethod
            def delete(_):
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        scheduler = scheduler_module.TaskScheduler()

        parsed = scheduler._parse_goal_command("goal:Fix bug|context:Production|team:devops")
        assert parsed["goal"] == "Fix bug"
        assert parsed["context"] == "Production"
        assert parsed["team_id"] == "devops"

    def test_parse_goal_command_handles_minimal(self, monkeypatch):
        class StubRepo:
            @staticmethod
            def get_all():
                return []

            @staticmethod
            def save(task):
                return task

            @staticmethod
            def delete(_):
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        scheduler = scheduler_module.TaskScheduler()

        parsed = scheduler._parse_goal_command("goal:Simple task")
        assert parsed["goal"] == "Simple task"
        assert parsed["context"] == ""
        assert parsed["team_id"] == ""

    def test_parse_goal_command_returns_empty_for_non_goal(self, monkeypatch):
        class StubRepo:
            @staticmethod
            def get_all():
                return []

            @staticmethod
            def save(task):
                return task

            @staticmethod
            def delete(_):
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        scheduler = scheduler_module.TaskScheduler()

        parsed = scheduler._parse_goal_command("echo hello")
        assert parsed["goal"] == ""
        assert parsed["context"] == ""
        assert parsed["team_id"] == ""

    def test_execute_goal_task_handles_invalid_goal(self, monkeypatch):
        class StubRepo:
            @staticmethod
            def get_all():
                return []

            @staticmethod
            def save(task):
                task.last_run = time.time()
                return task

            @staticmethod
            def delete(_):
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        scheduler = scheduler_module.TaskScheduler()

        task = ScheduledTaskDB(id="invalid-task", command="echo hello", interval_seconds=60, next_run=time.time() + 60)

        result = scheduler._execute_goal_task(task)
        assert result is False

    def test_execute_goal_task_handles_empty_goal(self, monkeypatch):
        class StubRepo:
            @staticmethod
            def get_all():
                return []

            @staticmethod
            def save(task):
                task.last_run = time.time()
                return task

            @staticmethod
            def delete(_):
                return True

        monkeypatch.setattr(scheduler_module, "scheduled_task_repo", StubRepo())
        scheduler = scheduler_module.TaskScheduler()

        task = ScheduledTaskDB(id="empty-goal", command="goal:", interval_seconds=60, next_run=time.time() + 60)

        result = scheduler._execute_goal_task(task)
        assert result is False
