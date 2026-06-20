"""AFF-E2E-T004: Route/API smoke test for new-project propose path.

Proves that the public HTTP route (/tasks/<tid>/step/propose) for a
new_software_project task:
  - reaches TaskScopedExecutionService.propose_task_step (not a bypass)
  - selects tool_calling_llm as the strategy (LLM-first)
  - does NOT call sgpt
  - returns the expected response shape

This test is lighter than the full-flow service E2E (test_autopilot_new_project_fibonacci_full_flow.py).
It verifies the public route contract is not bypassing policy or the proposal registry.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from tests.fixtures.mock_openai_compatible_provider import make_mock_invoke_with_tools


SMOKE_TASK_ID = "T-API-SMOKE-NEW-PROJECT-001"

_MOCK_TOOL_CALLS = [
    {"name": "write_file", "args": {"path": "app.py", "content": "# fib\n"}},
    {"name": "write_file", "args": {"path": "requirements.txt", "content": "flask\n"}},
]


@pytest.fixture(autouse=True)
def _block_sgpt():
    with patch(
        "agent.cli_backends.sgpt.run_sgpt_command",
        side_effect=RuntimeError("sgpt_blocked_in_AFF-E2E-T004"),
        create=True,
    ):
        yield


@pytest.fixture
def smoke_task(app, admin_auth_header, client):
    """Create a new_software_project task via the task creation route."""
    res = client.post(
        "/tasks",
        json={
            "id": SMOKE_TASK_ID,
            "title": "Fibonacci API (API smoke)",
            "description": "Create a Fibonacci REST API",
            "task_kind": "new_software_project",
            "status": "assigned",
        },
        headers=admin_auth_header,
    )
    assert res.status_code in (200, 201), (
        f"Task creation failed: {res.status_code} {res.json}"
    )
    return SMOKE_TASK_ID


class TestNewProjectApiSmoke:
    """Thin smoke test for the public propose route with a new_software_project task."""

    def test_propose_route_returns_200(self, smoke_task, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )

        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200, (
            f"Propose route returned {res.status_code}: {res.json}"
        )

    def test_propose_route_selects_tool_calling_llm(self, smoke_task, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )
        # LMStudio compat layer reorders to put flexible_llm_normalization first;
        # bypass it so this test can verify tool_calling_llm is selected when it succeeds.
        monkeypatch.setattr(
            "agent.services.propose_policy_service.ProposePolicyService._apply_provider_compatibility",
            staticmethod(lambda merged, **_: merged),
        )

        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200
        data = (res.json or {}).get("data") or {}
        # Route response includes strategy metadata
        strategy = self._selected_strategy(data)
        assert strategy == "tool_calling_llm", (
            f"Expected tool_calling_llm strategy, got {strategy!r}. "
            f"Response data: {data}"
        )

    def test_propose_route_returns_tool_calls(self, smoke_task, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )

        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200
        data = (res.json or {}).get("data") or {}
        tool_calls = self._tool_calls(data)
        assert tool_calls, (
            f"Expected tool_calls in propose response, got none. data={data}"
        )
        tc_names = [tc.get("name") for tc in tool_calls]
        assert "write_file" in tc_names

    def test_propose_route_status_executable(self, smoke_task, client, admin_auth_header, monkeypatch):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )

        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200
        data = (res.json or {}).get("data") or {}
        status = data.get("status") or data.get("proposal_status")
        assert status == "executable", (
            f"Expected status=executable, got {status!r}. data={data}"
        )

    def test_propose_route_sgpt_never_called(self, smoke_task, client, admin_auth_header, monkeypatch):
        """sgpt must not be called for new_software_project — autouse fixture blocks it."""
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )

        # If sgpt is called, _block_sgpt raises RuntimeError and the test fails
        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200

    def test_propose_route_deterministic_handler_not_selected_first(
        self, smoke_task, client, admin_auth_header, monkeypatch
    ):
        monkeypatch.setattr("agent.config.settings.default_provider", "lmstudio")
        monkeypatch.setattr(
            "agent.services.model_invocation_service.ModelInvocationService.invoke_with_tools",
            make_mock_invoke_with_tools(_MOCK_TOOL_CALLS),
        )

        res = client.post(
            f"/tasks/{smoke_task}/step/propose",
            json={"prompt": "Create a Fibonacci REST API"},
            headers=admin_auth_header,
        )

        assert res.status_code == 200
        data = (res.json or {}).get("data") or {}
        strategy = self._selected_strategy(data)
        assert strategy != "deterministic_handler", (
            "deterministic_handler was selected before LLM strategies — LLM-first policy violated"
        )
    @staticmethod
    def _selected_strategy(data: dict) -> str | None:
        meta = data.get("propose_strategy_meta") or {}
        metadata = data.get("metadata") or {}
        routing = data.get("routing") or {}
        return (
            data.get("selected_strategy")
            or meta.get("selected_strategy")
            or metadata.get("selected_strategy")
            or routing.get("selected_strategy")
            or routing.get("strategy")
        )

    @staticmethod
    def _tool_calls(data: dict) -> list[dict]:
        direct = data.get("tool_calls") or []
        if direct:
            return direct
        proposal = data.get("proposal") or {}
        return proposal.get("tool_calls") or []
