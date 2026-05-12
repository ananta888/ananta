"""FA-T019: Tests for task artifact-status API endpoint.

Verifies that GET /api/tasks/<tid>/artifact-status returns:
- attempted_strategies, selected_strategy, proposal_status, proposal_reason, normalization_format
- artifact_summary (completion_decision, reason_codes, manifest_status, verification_status)
- No raw secrets or full artifact content by default
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, Mock


def _make_task_dict(tid="task-001", status="proposed", last_proposal=None, history=None, verification_status=None):
    return {
        "id": tid,
        "status": status,
        "last_proposal": last_proposal or {},
        "history": history or [],
        "verification_status": verification_status or {},
    }


def _call_handler(task_dict, tid="task-001"):
    """Call get_task_artifact_status handler directly with mocked repo."""
    from agent.routes.tasks.artifact_status import get_task_artifact_status
    from flask import Flask

    app = Flask(__name__)

    task_mock = Mock()
    task_mock.model_dump.return_value = task_dict

    with app.test_request_context(f"/api/tasks/{tid}/artifact-status"):
        with patch("agent.routes.tasks.artifact_status.get_repository_registry") as mock_repos:
            mock_repos.return_value.task_repo.get_by_id.return_value = task_mock
            response, status_code = get_task_artifact_status(tid)
    return json.loads(response.data), status_code


def _call_handler_not_found(tid="missing"):
    from agent.routes.tasks.artifact_status import get_task_artifact_status
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context(f"/api/tasks/{tid}/artifact-status"):
        with patch("agent.routes.tasks.artifact_status.get_repository_registry") as mock_repos:
            mock_repos.return_value.task_repo.get_by_id.return_value = None
            response = get_task_artifact_status(tid)
    # api_response returns a Response, not a tuple in 404 case
    if isinstance(response, tuple):
        return json.loads(response[0].data), response[1]
    return json.loads(response.data), response.status_code


class TestArtifactStatusAPIFields:
    """artifact-status endpoint returns all FA-T019 fields."""

    def test_returns_attempted_strategies_from_last_proposal(self):
        propose_meta = {
            "attempted_strategies": [
                {"strategy_id": "deterministic_handler", "status": "executable", "reason": None},
            ],
            "selected_strategy": "deterministic_handler",
            "proposal_status": "executable",
            "proposal_reason": "template_applied",
            "normalization_format": None,
        }
        task_dict = _make_task_dict(
            last_proposal={
                "routing": {
                    "task_kind": "new_software_project",
                    "propose_strategy_meta": propose_meta,
                },
                "reason": "template_applied",
            }
        )
        data, status = _call_handler(task_dict)
        assert status == 200
        assert "attempted_strategies" in data
        assert len(data["attempted_strategies"]) == 1
        assert data["attempted_strategies"][0]["strategy_id"] == "deterministic_handler"
        assert data["selected_strategy"] == "deterministic_handler"
        assert data["proposal_status"] == "executable"
        assert data["proposal_reason"] == "template_applied"

    def test_returns_empty_strategies_when_no_proposal(self):
        data, status = _call_handler(_make_task_dict())
        assert status == 200
        assert data["attempted_strategies"] == []
        assert data["selected_strategy"] is None

    def test_returns_normalization_format(self):
        propose_meta = {
            "attempted_strategies": [
                {"strategy_id": "tool_calling_llm", "status": "executable", "reason": None},
            ],
            "selected_strategy": "tool_calling_llm",
            "proposal_status": "executable",
            "proposal_reason": None,
            "normalization_format": "openai_tool_calls",
        }
        task_dict = _make_task_dict(
            last_proposal={"routing": {"propose_strategy_meta": propose_meta}}
        )
        data, status = _call_handler(task_dict)
        assert status == 200
        assert data["normalization_format"] == "openai_tool_calls"

    def test_returns_artifact_summary_with_completion_decision(self):
        history = [{
            "event_type": "artifact_first_completion",
            "details": {
                "completion_decision": "completed",
                "reason_codes": ["required_paths_present", "exit_code_zero"],
                "manifest_id": "manifest-001",
                "artifact_ids": ["art-001", "art-002"],
                "advisory_parse_status": "ignored",
            }
        }]
        task_dict = _make_task_dict(
            status="completed",
            history=history,
            verification_status={"status": "passed"},
        )
        data, status = _call_handler(task_dict)
        assert status == 200
        assert data["artifact_summary"]["completion_decision"] == "completed"
        assert "required_paths_present" in data["artifact_summary"]["reason_codes"]
        assert data["artifact_summary"]["manifest_status"] == "valid"
        assert data["verification_status"] == "passed"
        assert data["completion_decision"] == "completed"

    def test_returns_task_not_found(self):
        data, status = _call_handler_not_found("missing-task")
        assert status == 404

    def test_strategy_meta_from_history_event_when_no_last_proposal(self):
        """Falls back to history proposal_result event for strategy meta."""
        history = [{
            "event_type": "proposal_result",
            "propose_strategy_meta": {
                "attempted_strategies": [
                    {"strategy_id": "deterministic_handler", "status": "declined", "reason": "no_handler"},
                    {"strategy_id": "worker_strategy", "status": "executable", "reason": None},
                ],
                "selected_strategy": "worker_strategy",
                "proposal_status": "executable",
                "proposal_reason": None,
                "normalization_format": "openai_tool_calls",
            }
        }]
        data, status = _call_handler(_make_task_dict(history=history))
        assert status == 200
        assert len(data["attempted_strategies"]) == 2
        assert data["selected_strategy"] == "worker_strategy"
        assert data["normalization_format"] == "openai_tool_calls"

    def test_no_secrets_in_response(self):
        """Response must not include raw command / secrets from last_proposal."""
        task_dict = _make_task_dict(
            last_proposal={
                "routing": {"propose_strategy_meta": {}},
                "raw": "SECRET_KEY=abc123 python run.py",
                "model": "gpt-4",
            }
        )
        data, status = _call_handler(task_dict)
        assert status == 200
        response_str = json.dumps(data)
        assert "SECRET_KEY=abc123" not in response_str


class TestOrchestratorAttemptedStrategiesTracking:
    """ProposeStrategyOrchestrator correctly populates attempted_strategies."""

    def test_first_success_records_one_attempt(self):
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext, ProposeStrategy
        from worker.core.propose import ProposeStrategyResult, ExecutableProposal, STATUS_EXECUTABLE
        from agent.services.propose_policy import ProposePolicy

        class AlwaysExecutable(ProposeStrategy):
            def run(self, ctx):
                ep = ExecutableProposal.from_command(
                    goal_id=ctx.goal_id, task_id=ctx.task_id,
                    strategy_id="test", command="echo hi",
                )
                return ProposeStrategyResult.executable("test", ep)

        policy = ProposePolicy(strategy_order=["test"], on_all_strategies_declined="needs_review")
        orch = ProposeStrategyOrchestrator(policy, {"test": AlwaysExecutable()})
        result = orch.run(ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="x"))

        assert result.status == STATUS_EXECUTABLE
        assert result.metadata["selected_strategy"] == "test"
        assert len(result.metadata["attempted_strategies"]) == 1
        assert result.metadata["attempted_strategies"][0]["status"] == STATUS_EXECUTABLE

    def test_fallback_records_all_attempts(self):
        from worker.core.propose_orchestrator import ProposeStrategyOrchestrator, ProposeContext, ProposeStrategy
        from worker.core.propose import ProposeStrategyResult
        from agent.services.propose_policy import ProposePolicy

        class AlwaysDeclined(ProposeStrategy):
            def __init__(self, sid): self.sid = sid
            def run(self, ctx): return ProposeStrategyResult.declined(self.sid, "test_declined")

        policy = ProposePolicy(
            strategy_order=["s1", "s2"],
            on_all_strategies_declined="needs_review",
            max_strategy_attempts=2,  # allow both strategies to run
        )
        orch = ProposeStrategyOrchestrator(
            policy, {"s1": AlwaysDeclined("s1"), "s2": AlwaysDeclined("s2")}
        )
        result = orch.run(ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="x"))

        assert result.status == "needs_review"
        assert result.metadata["selected_strategy"] is None
        assert len(result.metadata["attempted_strategies"]) == 2
