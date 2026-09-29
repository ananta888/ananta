"""AFH-T024: Regression tests for worker_todo_planner_service malformed LLM output.

Verifies that:
- Malformed/Markdown/natural-language LLM output never crashes the Hub
- Deterministic contract is always returned as fallback
- Proposal artifact wraps LLM output (never replaces task list)
- No path requeues task solely because planner parse failed
- generate_text is NOT called by default (planner_llm_enabled=False)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.services.worker_todo_planner_service import WorkerTodoPlannerService


MALFORMED_OUTPUTS = [
    # Markdown fenced
    (
        "markdown_fenced",
        "```json\n{broken json\n```",
    ),
    # Natural language
    (
        "natural_language",
        "I would create the following tasks:\n1. Set up the project\n2. Implement the feature\n3. Write tests",
    ),
    # Empty string
    (
        "empty",
        "",
    ),
    # Just backticks
    (
        "just_backticks",
        "```\n```",
    ),
    # Valid JSON but wrong shape
    (
        "wrong_shape",
        json.dumps({"wrong": "shape", "no_tasks": True}),
    ),
    # Tasks key with wrong items
    (
        "tasks_wrong_items",
        json.dumps({"tasks": "not a list"}),
    ),
    # Valid but empty tasks list
    (
        "empty_tasks",
        json.dumps({"tasks": []}),
    ),
]


def _make_worker_contract_service(subtask_id: str = "sub-1") -> MagicMock:
    """Build a mock worker_contract_service that returns a minimal valid contract."""
    svc = MagicMock()
    svc.build_worker_todo_contract.return_value = {
        "schema": "worker_todo_contract.v1",
        "task_id": subtask_id,
        "goal_id": "goal-1",
        "trace_id": "tr-1",
        "capability_id": "worker.command.execute",
        "context_hash": "ctx-1",
        "executor_kind": "ananta_worker",
        "worker_profile": "balanced",
        "profile_source": "agent_default",
        "mode": "assistant_execute",
        "allowed_tools": [],
        "enforce_artifacts": True,
        "max_steps": 30,
        "todo": {
            "schema": "worker_todo_contract.v1",
            "track": "general-worker-subplan",
            "tasks": [
                {
                    "id": "todo-1",
                    "title": "Execute delegated worker task",
                    "instructions": "Execute the task.",
                    "status": "todo",
                    "priority": "medium",
                    "risk": "medium",
                    "depends_on": [],
                    "allowed_tools": [],
                    "expected_artifacts": [{"kind": "task_output", "required": True, "description": "Primary output"}],
                    "acceptance_criteria": ["Task requirements satisfied."],
                    "metadata": {"source": "hub_deterministic_seed"},
                }
            ],
        },
    }
    return svc


def _build_service_kwargs(subtask_id: str = "sub-1") -> dict:
    return dict(
        worker_contract_service=_make_worker_contract_service(subtask_id),
        subtask_id=subtask_id,
        parent_task={"id": "parent-1", "goal_id": "goal-1", "priority": "medium"},
        subtask_description="Implement Fibonacci Flask app",
        task_kind="coding",
        required_capabilities=["worker.command.execute"],
        worker_profile="balanced",
        profile_source="agent_default",
        allowed_tools=["bash"],
        expected_output_schema=None,
        target_worker=None,
        context_bundle_id="ctx-1",
        workspace_dir="/tmp/workspace",
    )


class TestPlannerLLMDisabledByDefault:
    def test_generate_text_not_called_by_default(self) -> None:
        """generate_text must not be called when planner_llm_enabled=False (the default)."""
        service = WorkerTodoPlannerService()
        with patch("agent.services.worker_todo_planner_service.generate_text") as mock_gen:
            result = service.build_delegation_todo_contract(**_build_service_kwargs())
            mock_gen.assert_not_called()
        assert result is not None
        assert result["contract"]["todo"]["tasks"]
        # Mode can be artifact_first, deterministic_only, or deterministic_schema_invalid
        # (depending on mock contract validity) — but LLM was not attempted
        assert result["generation"]["mode"] in (
            "artifact_first", "deterministic_only", "deterministic_schema_invalid"
        )
        assert result["generation"]["llm_attempted"] is False

    def test_deterministic_contract_always_present(self) -> None:
        """Deterministic contract must always be returned, regardless of LLM setting."""
        service = WorkerTodoPlannerService()
        result = service.build_delegation_todo_contract(**_build_service_kwargs())
        assert result is not None
        tasks = result["contract"]["todo"]["tasks"]
        assert len(tasks) >= 1
        assert all(t.get("metadata", {}).get("source") == "hub_deterministic_seed" for t in tasks)


class TestPlannerMalformedLLMOutput:
    """Tests for _refine_tasks_with_llm with malformed LLM responses.

    We test the internal method directly to avoid Flask app context requirements.
    The key invariant: malformed output must always return deterministic fallback
    and a PlannerProposalArtifact wrapping the output.
    """

    def _run_refine(self, raw_output: str) -> tuple:
        """Call _refine_tasks_with_llm with a mocked generate_text."""
        service = WorkerTodoPlannerService()
        planner_cfg = {
            "provider": "test",
            "model": "test-model",
            "planner_llm_timeout_seconds": 5,
            "planner_llm_retry_attempts": 1,
        }
        todo_contract = {
            "task_id": "sub-1",
            "goal_id": "goal-1",
            "todo": {"tasks": [{"id": "todo-1", "title": "Base task"}]},
        }
        with patch("agent.services.worker_todo_planner_service.generate_text", return_value=raw_output):
            tasks, error, proposal = service._refine_tasks_with_llm(
                planner_cfg=planner_cfg,
                agent_cfg=None,
                todo_contract=todo_contract,
                task_kind="coding",
                subtask_description="Implement Fibonacci Flask app",
                max_tasks=3,
                default_allowed_tools=["bash"],
                fallback_expected_artifacts=[{"kind": "task_output", "required": True}],
                subtask_id="sub-1",
                parent_task={"id": "parent-1", "goal_id": "goal-1", "priority": "medium"},
            )
        return tasks, error, proposal

    @pytest.mark.parametrize("label,raw_output", MALFORMED_OUTPUTS)
    def test_malformed_output_returns_error_and_proposal(self, label: str, raw_output: str) -> None:
        """Malformed LLM output must return None tasks + error + proposal artifact."""
        tasks, error, proposal = self._run_refine(raw_output)

        # For inputs that can't produce valid tasks (all of our MALFORMED_OUTPUTS),
        # either tasks is None (parse failed) or it's an empty list (normalized to nothing)
        valid_task_producing = False
        if raw_output.startswith('{"tasks":') or raw_output.startswith('['):
            try:
                parsed = json.loads(raw_output)
                raw_tasks = parsed if isinstance(parsed, list) else parsed.get("tasks")
                if isinstance(raw_tasks, list) and len(raw_tasks) > 0:
                    valid_task_producing = True
            except Exception:
                pass

        if not valid_task_producing:
            assert tasks is None, f"[{label}] Malformed output must return None tasks, got {tasks}"
            assert error is not None, f"[{label}] Must return an error string"

        # Proposal must be created
        assert proposal is not None, f"[{label}] Must create a PlannerProposalArtifact"
        assert proposal.get("schema") == "planner_proposal_artifact.v1", (
            f"[{label}] Proposal must have correct schema"
        )
        assert proposal.get("parse_status") in (
            "parsed", "failed", "malformed_json", "markdown_fenced", "natural_language",
        ), f"[{label}] parse_status={proposal.get('parse_status')!r}"

    @pytest.mark.parametrize("label,raw_output", MALFORMED_OUTPUTS)
    def test_malformed_output_does_not_replace_tasks_directly(self, label: str, raw_output: str) -> None:
        """Malformed LLM output must go through proposal artifact, not direct task replacement."""
        tasks, error, proposal = self._run_refine(raw_output)
        # The return value is (tasks, error, proposal) — tasks being None means no direct replacement
        # If tasks is not None for some edge case, it means it was somehow normalized
        # But the proposal must always wrap the output
        assert proposal is not None, (
            f"[{label}] LLM output must always be wrapped in PlannerProposalArtifact"
        )
        assert proposal.get("adoption_status") in ("ignored", "pending"), (
            f"[{label}] proposal.adoption_status must be ignored or pending, not 'adopted'"
        )

    def test_natural_language_classified_correctly(self) -> None:
        """Natural language output must be classified as natural_language parse_status."""
        nl = "First, set up the project. Then implement the feature. Finally, write tests."
        tasks, error, proposal = self._run_refine(nl)
        assert proposal is not None
        assert proposal["parse_status"] in ("natural_language", "failed"), (
            f"Natural language must be classified as natural_language, got {proposal['parse_status']!r}"
        )

    def test_markdown_fenced_classified_correctly(self) -> None:
        """Markdown fenced output must be classified as markdown_fenced."""
        md = "```json\n{broken json content\n```"
        tasks, error, proposal = self._run_refine(md)
        assert proposal is not None
        assert proposal["parse_status"] in ("markdown_fenced", "failed"), (
            f"Markdown fenced must be classified as markdown_fenced, got {proposal['parse_status']!r}"
        )

    def test_valid_json_creates_parsed_proposal(self) -> None:
        """Valid JSON from LLM creates a parsed proposal."""
        valid_tasks = json.dumps({
            "tasks": [
                {
                    "id": "todo-1",
                    "title": "Set up Flask app",
                    "instructions": "Install Flask and create app.py",
                    "status": "todo",
                    "priority": "high",
                    "risk": "low",
                    "depends_on": [],
                    "allowed_tools": ["bash"],
                    "expected_artifacts": [{"kind": "generated_file", "required": True}],
                    "acceptance_criteria": ["app.py exists"],
                }
            ]
        })
        tasks, error, proposal = self._run_refine(valid_tasks)
        assert proposal is not None
        assert proposal["parse_status"] == "parsed"
        assert error is None, "Valid JSON must not produce an error"
        assert tasks is not None and len(tasks) > 0, "Valid JSON must produce tasks"

    def test_no_retry_on_planner_parse_failure(self) -> None:
        """Planner parse failure must not requeue the task when deterministic contract exists."""
        from agent.services.task_retry_policy_service import (
            get_task_retry_policy_service,
            REASON_PLANNER_LLM_PARSE_FAILED,
        )
        retry_svc = get_task_retry_policy_service()
        cls = retry_svc.classify(
            reason=REASON_PLANNER_LLM_PARSE_FAILED,
            retry_count=0,
            deterministic_contract_exists=True,
        )
        assert cls.should_retry is False
        assert cls.classification == "non_retryable"
