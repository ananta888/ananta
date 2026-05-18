from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.runtime_policy import TASK_KINDS, normalize_task_kind
from agent.services.goal_workspace_coordinator_service import GoalWorkspaceCoordinatorService


@pytest.fixture
def coordinator():
    return GoalWorkspaceCoordinatorService()


class TestGoalWorkspaceCoordinatorService:
    def test_register_and_get_candidates(self, coordinator):
        coordinator.register_branch("goal-1", "goal/abc123", "http://worker1")
        coordinator.mark_branch_ready("goal-1", "goal/abc123")
        assert "goal/abc123" in coordinator.get_merge_candidates("goal-1")

    def test_not_ready_branch_not_in_candidates(self, coordinator):
        coordinator.register_branch("goal-2", "goal/xyz", "http://worker2")
        assert coordinator.get_merge_candidates("goal-2") == []

    def test_goal_isolation(self, coordinator):
        coordinator.register_branch("goal-A", "goal/branchA")
        coordinator.mark_branch_ready("goal-A", "goal/branchA")
        assert coordinator.get_merge_candidates("goal-B") == []

    def test_empty_goal_returns_empty_list(self, coordinator):
        assert coordinator.get_merge_candidates("nonexistent-goal") == []

    def test_register_branch_is_idempotent(self, coordinator):
        coordinator.register_branch("goal-3", "goal/abc", "http://w1")
        coordinator.register_branch("goal-3", "goal/abc", "http://w2")
        coordinator.mark_branch_ready("goal-3", "goal/abc")
        candidates = coordinator.get_merge_candidates("goal-3")
        assert candidates.count("goal/abc") == 1


class TestGoalCompletionCreatesMergeTask:
    def test_creates_merge_task_when_enabled(self, coordinator):
        coordinator.register_branch("goal-m1", "goal/m1")
        coordinator.mark_branch_ready("goal-m1", "goal/m1")

        mock_tqs = MagicMock()
        effective_config = {
            "git_workspace": {
                "enabled": True,
                "merge_strategy": "squash",
                "target_branch": "main",
            }
        }
        result = coordinator.create_merge_task_after_goal_completion(
            goal_id="goal-m1",
            effective_config=effective_config,
            task_queue_service=mock_tqs,
        )
        assert result is not None
        assert result["task_kind"] == "git_merge"
        assert "goal/m1" in result["source_branches"]
        assert result["merge_strategy"] == "squash"
        mock_tqs.ingest_task.assert_called_once()

    def test_no_merge_task_when_git_workspace_disabled(self, coordinator):
        coordinator.register_branch("goal-m2", "goal/m2")
        coordinator.mark_branch_ready("goal-m2", "goal/m2")
        mock_tqs = MagicMock()
        result = coordinator.create_merge_task_after_goal_completion(
            goal_id="goal-m2",
            effective_config={"git_workspace": {"enabled": False}},
            task_queue_service=mock_tqs,
        )
        assert result is None
        mock_tqs.ingest_task.assert_not_called()

    def test_no_merge_task_when_no_ready_branches(self, coordinator):
        mock_tqs = MagicMock()
        result = coordinator.create_merge_task_after_goal_completion(
            goal_id="goal-m3",
            effective_config={"git_workspace": {"enabled": True}},
            task_queue_service=mock_tqs,
        )
        assert result is None

    def test_merge_task_contains_correct_fields(self, coordinator):
        coordinator.register_branch("goal-m4", "goal/feat1")
        coordinator.register_branch("goal-m4", "goal/feat2")
        coordinator.mark_branch_ready("goal-m4", "goal/feat1")
        coordinator.mark_branch_ready("goal-m4", "goal/feat2")
        mock_tqs = MagicMock()
        result = coordinator.create_merge_task_after_goal_completion(
            goal_id="goal-m4",
            effective_config={"git_workspace": {"enabled": True, "merge_strategy": "merge"}},
            task_queue_service=mock_tqs,
        )
        assert len(result["source_branches"]) == 2
        assert result["merge_strategy"] == "merge"


class TestGitMergeInTaskKinds:
    def test_git_merge_in_task_kinds(self):
        assert "git_merge" in TASK_KINDS

    def test_normalize_git_merge(self):
        assert normalize_task_kind("git_merge", "") == "git_merge"

    def test_normalize_git_commit_still_works(self):
        assert normalize_task_kind("git_commit", "") == "git_commit"

    def test_existing_kinds_unchanged(self):
        for kind in ("coding", "analysis", "doc", "ops", "research"):
            assert normalize_task_kind(kind, "") == kind
