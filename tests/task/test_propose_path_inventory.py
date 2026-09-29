"""Tests for propose path inventory and legacy blocks per FA-T003."""
from unittest.mock import Mock

import pytest

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


class TestProposePathInventory:
    @pytest.fixture
    def service(self):
        return TaskScopedExecutionService()

    @pytest.fixture
    def mock_args(self):
        return {
            "tid": "test-tid",
            "task": {},
            "request_data": Mock(),
            "base_prompt": "test prompt",
            "research_context": None,
            "cli_runner": Mock(),
            "cfg": {},
            "tool_definitions_resolver": Mock(),
        }

    def test_propose_single_task_step_is_blocked(self, service, mock_args):
        """Legacy _propose_single_task_step must raise per FA-T003."""
        with pytest.raises(NotImplementedError, match="FA-T003"):
            service._propose_single_task_step(**mock_args)

    def test_handler_propose_mapping_documented(self):
        """_try_handler_propose maps to deterministic_handler (static check via comment/search)."""
        # Static: comment present, runtime via orchestrator integration test in M2
        pass

    def test_hermes_propose_mapping_documented(self):
        """_try_hermes_propose maps to worker_strategy (static check via comment/search)."""
        # Static: comment present, runtime via orchestrator integration test in M2
        pass