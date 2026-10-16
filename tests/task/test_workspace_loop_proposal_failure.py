"""A failed delegated mutation must never be repaired into an execute command."""

from types import SimpleNamespace

from agent.services import _task_scoped_propose_single as proposals
from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def test_failed_worker_loop_stops_before_outer_proposal_repair(monkeypatch):
    run = SimpleNamespace(
        backend_used="ananta-worker",
        rc=1,
        raw_res='{"kind":"loop_aborted","reason":"invalid_output_limit_reached"}',
        cli_err="",
    )
    deps = SimpleNamespace(route_response=TaskScopedRouteResponse)
    monkeypatch.setattr(proposals.ProposeCollaborators, "load", lambda: deps)
    monkeypatch.setattr(proposals, "_start_propose_run", lambda **kwargs: run)
    monkeypatch.setattr(proposals, "_prepare_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(proposals, "_invoke_primary_cli", lambda *args: {})

    def forbidden_repair(*args, **kwargs):
        raise AssertionError("failed mutation was passed to proposal repair")

    monkeypatch.setattr(proposals, "repair_proposal", forbidden_repair)
    result = proposals.propose_single_task_step(
        tid="task",
        task={},
        request_data=SimpleNamespace(),
        base_prompt="Fix app",
        research_context=None,
        cli_runner=lambda: None,
        cfg={},
        tool_definitions_resolver=lambda: [],
        allow_legacy_path=True,
        resolve_requested_model=lambda: None,
        invoke_cli_runner=lambda: None,
        coalesce_cli_output=lambda: None,
    )
    assert result.code == 502
    assert result.message == "worker_proposal_failed"
    assert result.data["returncode"] == 1
    assert "command" not in result.data
