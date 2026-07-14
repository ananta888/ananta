from __future__ import annotations

from unittest.mock import patch

from agent.cli.commands import runtime
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _run(run_id: str = "run-1") -> dict:
    return {
        "schema": "ananta.workflow_runtime_operations_record.v1",
        "run_id": run_id,
        "runtime": "native",
        "mode": "production",
        "status": "completed",
        "outcome_claim": "unverified",
        "degraded": True,
        "stale": False,
        "verified_evidence_count": 0,
        "open_gate_count": 1,
    }


def _list_projection() -> dict:
    return {
        "schema": "ananta.workflow_runtime_operations_list.v1",
        "summary": {
            "total_runs": 1,
            "degraded_runs": 1,
            "stale_runs": 0,
            "unverified_successes": 1,
            "open_gates": 1,
        },
        "runs": [_run()],
    }


def test_cli_operations_reads_hub_projection_and_preserves_unverified_outcome(capsys) -> None:
    with patch("agent.cli.api_client.get_api_client") as factory:
        factory.return_value.get.return_value = _list_projection()
        result = runtime.dispatch(["operations", "--health", "degraded"])

    assert result == 0
    assert factory.return_value.get.call_args.args == ("/api/workflow-runtime/operations",)
    assert factory.return_value.get.call_args.kwargs["params"]["health"] == "degraded"
    output = capsys.readouterr().out
    assert "unverified=1" in output
    assert "run-1" in output


def test_cli_run_reads_same_hub_projection_detail(capsys) -> None:
    with patch("agent.cli.api_client.get_api_client") as factory:
        factory.return_value.get.return_value = {"status": "ok", "run": _run("run/a")}
        result = runtime.dispatch(["run", "run/a"])

    assert result == 0
    assert factory.return_value.get.call_args.args == (
        "/api/workflow-runtime/operations/runs/run%2Fa",
    )
    assert "Status:    unverified" in capsys.readouterr().out


def test_tui_runtime_operations_uses_authenticated_hub_contract() -> None:
    state = OperatorState(endpoint="http://hub.local", audit_context={"token": "user-jwt"})
    with patch("client_surfaces.operator_tui.commands_ops.OpsApiClient") as client_class:
        client_class.return_value.workflow_runtime_operations.return_value = _list_projection()
        result = execute_command(":ops runtime degraded", state)

    client_class.assert_called_once_with("http://hub.local", token="user-jwt")
    client_class.return_value.workflow_runtime_operations.assert_called_once_with(health="degraded")
    assert result.handled is True
    assert result.state.section_id == "ops"
    assert result.state.section_payloads["workflow_runtime"]["runs"][0]["outcome_claim"] == "unverified"


def test_cli_and_tui_accept_explicit_operator_token_without_password_login(monkeypatch) -> None:
    from agent.cli import api_client

    monkeypatch.setenv("ANANTA_AUTH_TOKEN", "explicit-user-jwt")
    monkeypatch.setattr(api_client, "_load_dotenv_fallback", lambda: {})
    assert api_client._auth_token("http://hub.local") == "explicit-user-jwt"

    state = OperatorState(endpoint="http://hub.local")
    with patch("client_surfaces.operator_tui.commands_ops.OpsApiClient") as client_class:
        client_class.return_value.workflow_runtime_operations.return_value = _list_projection()
        result = execute_command(":ops runtime", state)
    assert result.handled is True
    client_class.assert_called_once_with("http://hub.local", token="explicit-user-jwt")


def test_clients_fail_closed_for_non_projection_payload(capsys) -> None:
    with patch("agent.cli.api_client.get_api_client") as factory:
        factory.return_value.get.return_value = {"status": "error", "reason_code": "forbidden"}
        cli_result = runtime.dispatch(["operations"])
    assert cli_result == 4
    assert "forbidden" in capsys.readouterr().err

    state = OperatorState(endpoint="http://hub.local", audit_context={"token": "user-jwt"})
    with patch("client_surfaces.operator_tui.commands_ops.OpsApiClient") as client_class:
        client_class.return_value.workflow_runtime_operations.return_value = {
            "status": "error",
            "reason_code": "forbidden",
        }
        tui_result = execute_command(":ops runtime", state)
    assert tui_result.handled is False
    assert "forbidden" in tui_result.state.status_message
