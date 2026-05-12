"""AFF-E2E final gap: task-scoped persist -> execute reload and verified completion.

This supplements test_autopilot_new_project_fibonacci_full_flow.py.
That test proves propose -> execute_local_step -> files -> artifacts -> completion.
This file pins the two remaining integration gaps:

1. Public/task-scoped route flow persists last_proposal during /step/propose and
   /step/execute reloads that persisted proposal when no command/tool_calls are
   supplied explicitly.
2. TaskCompletionPolicyService can complete with verification_required=True
   when required artifacts are explicitly verified, and does not complete when
   verification is missing.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.services.task_completion_policy_service import get_task_completion_policy_service
from tests.fixtures.mock_openai_compatible_provider import make_mock_invoke_with_tools


TASK_ID = "T-AFF-TASKSCOPED-PERSIST-EXECUTE"


@pytest.fixture(autouse=True)
def _block_sgpt():
    """sgpt must never be called in this full-flow regression test."""
    with patch(
        "agent.common.sgpt.run_sgpt_command",
        side_effect=RuntimeError("sgpt_blocked_in_taskscoped_persist_execute"),
        create=True,
    ):
        yield


def _tool_calls_for_workspace(workspace: Path) -> list[dict]:
    return [
        {
            "name": "write_file",
            "args": {
                "path": str(workspace / "app.py"),
                "content": (
                    "def fibonacci(n):\n"
                    "    a, b = 0, 1\n"
                    "    for _ in range(n):\n"
                    "        a, b = b, a + b\n"
                    "    return a\n"
                ),
            },
        },
        {
            "name": "write_file",
            "args": {
                "path": str(workspace / "README.md"),
                "content": "# Fibonacci API\nGenerated through task-scoped persist/execute flow.\n",
            },
        },
    ]


class TestTaskScopedPersistExecuteReload:
    """Route-level prove: propose persists, execute reloads last_proposal."""

    def test_propose_route_persists_and_execute_route_reloads_last_proposal(
        self,
        app,
        client,
        admin_auth_header,
        tmp_path: Path,
        monkeypatch,
    ):
        workspace = tmp_path / "taskscoped-fib"
        workspace.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_tool_calls_for_workspace(workspace)),
        )

        # Create a real local task via API.
        create_res = client.post(
            "/tasks",
            json={
                "id": TASK_ID,
                "title": "Fibonacci API task-scoped full flow",
                "description": "Create a Fibonacci API and write project files",
                "task_kind": "new_software_project",
                "status": "assigned",
            },
            headers=admin_auth_header,
        )
        assert create_res.status_code in (200, 201), create_res.get_data(as_text=True)

        # Propose through the public/task-scoped route. This must persist last_proposal.
        propose_res = client.post(
            f"/tasks/{TASK_ID}/step/propose",
            json={"prompt": "Create a Fibonacci API and write app.py and README.md"},
            headers=admin_auth_header,
        )
        assert propose_res.status_code == 200, propose_res.get_data(as_text=True)
        propose_data = (propose_res.json or {}).get("data") or {}
        assert propose_data.get("status") == "executable"
        assert (propose_data.get("propose_strategy_meta") or {}).get("selected_strategy") == "tool_calling_llm"

        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status

            task = _get_local_task_status(TASK_ID)
            assert task is not None
            last_proposal = task.get("last_proposal") or {}
            assert last_proposal.get("tool_calls"), "propose route must persist LLM tool_calls as last_proposal"
            assert not last_proposal.get("command"), "this test expects execute to reload tool_calls, not command"

        # Execute with an empty request. This is the important regression check:
        # execute_task_step must reload persisted last_proposal itself.
        execute_res = client.post(
            f"/tasks/{TASK_ID}/step/execute",
            json={},
            headers=admin_auth_header,
        )
        assert execute_res.status_code == 200, execute_res.get_data(as_text=True)
        execute_data = (execute_res.json or {}).get("data") or {}
        assert execute_data.get("status") in {"completed", "success"}, execute_data

        assert (workspace / "app.py").exists(), "execute route did not apply persisted write_file tool_call"
        assert (workspace / "README.md").exists(), "execute route did not apply persisted write_file tool_call"
        assert "fibonacci" in (workspace / "app.py").read_text(encoding="utf-8").lower()

        with app.app_context():
            from agent.routes.tasks.utils import _get_local_task_status

            task = _get_local_task_status(TASK_ID)
            assert task is not None
            history = task.get("history") or []
            assert any(h.get("event_type") == "proposal_result" for h in history)
            assert any(h.get("event_type") == "execution_result" for h in history)


class TestVerifiedCompletionPolicy:
    """Completion policy regression: verification_required=True really gates completion."""

    def _collection(self, *, verified: bool) -> dict:
        return {
            "manifest_valid": True,
            "manifest_id": "manifest-verified-completion-test",
            "synthesized": True,
            "collection_method": "synthesized_from_diff",
            "errors": [],
            "warnings": [],
            "artifacts": [
                {
                    "artifact_id": "artifact-app-py",
                    "relative_path": "app.py",
                    "_exists": True,
                    "required": True,
                    "verification_status": "verified" if verified else "unverified",
                },
                {
                    "artifact_id": "artifact-readme-md",
                    "relative_path": "README.md",
                    "_exists": True,
                    "required": True,
                    "verification_status": "verified" if verified else "unverified",
                },
            ],
        }

    def test_verified_required_artifacts_complete_with_verification_required_true(self):
        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="T-VERIFIED-COMPLETE",
            goal_id="G-VERIFIED-COMPLETE",
            collection_result=self._collection(verified=True),
            exit_code=0,
            retry_count=0,
            expected_paths=["app.py", "README.md"],
            verification_required=True,
            allow_synthesized_manifest=True,
        )

        assert decision.decision == "completed", decision.to_dict()
        assert svc.to_status(decision) == "completed"

    def test_unverified_required_artifacts_do_not_complete_with_verification_required_true(self):
        svc = get_task_completion_policy_service()
        decision = svc.evaluate(
            task_id="T-UNVERIFIED-NEEDS-REVIEW",
            goal_id="G-UNVERIFIED-NEEDS-REVIEW",
            collection_result=self._collection(verified=False),
            exit_code=0,
            retry_count=0,
            expected_paths=["app.py", "README.md"],
            verification_required=True,
            allow_synthesized_manifest=True,
        )

        assert decision.decision == "needs_review", decision.to_dict()
        assert "verification_required_but_not_verified" in decision.reason_codes
        assert svc.to_status(decision) == "needs_review"
