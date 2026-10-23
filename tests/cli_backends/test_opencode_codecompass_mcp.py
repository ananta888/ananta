import http.client
import json
import os
import socket
import subprocess
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from agent.cli_backends.coding_agent_contract import ProcessExecutionResult
from agent.cli_backends.opencode import _run_opencode_subprocess, run_opencode_command
from agent.cli_backends.opencode_codecompass_mcp import opencode_codecompass_mcp
from agent.services.codecompass_retrieval_capability_service import bind_retrieval_capability
from agent.services.codecompass_task_capability import task_capability_scope
from tests.cli_backends.test_opencode_process_adapter import RecordingProcessRunner, _permit


def test_native_opencode_discovers_codecompass_without_provider_override(tmp_path):
    runner = RecordingProcessRunner(ProcessExecutionResult(0, "ok", "", "completed", 3))
    with (
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch("agent.cli_backends.opencode.resolve_opencode_runtime_config", return_value={"provider_config": None}),
    ):
        result = _run_opencode_subprocess(
            prompt="inspect", model=None, timeout=10, workdir=str(tmp_path), output_format=None, process_runner=runner
        )
    assert result[0] == 0
    config = json.loads(runner.calls[0][1]["environment"].get("OPENCODE_CONFIG_CONTENT", "{}"))
    assert "ananta_codecompass" in config.get("mcp", {})


def _rpc(endpoint, method, params=None, *, token=None, notification=False):
    url = urlsplit(endpoint.url)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        body["id"] = 1
    try:
        connection.request(
            "POST",
            url.path,
            json.dumps(body),
            {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {endpoint.token if token is None else token}",
            },
        )
        response = connection.getresponse()
        content = response.read()
        return response.status, json.loads(content) if response.status == 200 else None
    finally:
        connection.close()


def _capability(*, operations=None, expired=False):
    capability = bind_retrieval_capability(
        {
            "workspace_id": "test-workspace",
            "repository_id": "test-repository",
            "source_scope": "repository",
            "revision": "test-revision",
            "allowed_paths": ["src"],
            "allowed_index_ids": ["test-index"],
        },
        subject_id="test-subject",
        tenant_id="test-tenant",
        now_epoch=1 if expired else None,
    )
    capability.update(
        task_id="test-task",
        audience="test-worker",
        allowed_operations=(["ananta.tool.codecompass.search"] if operations is None else operations),
    )
    return capability


def test_wire_initialize_list_call_preserves_parent_context_and_closes_listener(tmp_path):
    capability = _capability()
    with (
        task_capability_scope(capability),
        patch("agent.cli_backends.opencode_codecompass_mcp.get_tool_loop_config", return_value={}),
        patch("agent.services.tools.execute_ananta_tool", return_value={"status": "ok", "data": "found"}) as execute,
        opencode_codecompass_mcp(workspace_dir=str(tmp_path)) as endpoint,
    ):
        status, reply = _rpc(endpoint, "initialize", {"protocolVersion": "2025-03-26"})
        assert status == 200
        assert reply["result"]["capabilities"] == {"tools": {}}
        assert _rpc(endpoint, "notifications/initialized", notification=True)[0] == 202
        status, reply = _rpc(endpoint, "tools/list")
        names = [row["name"] for row in reply["result"]["tools"]]
        assert "codecompass.search" in names
        assert all(name.startswith("codecompass.") for name in names)
        assert "test-subject" not in json.dumps(reply)
        status, reply = _rpc(endpoint, "tools/call", {"name": "codecompass.search", "arguments": {"query": "test"}})
        assert status == 200 and reply["result"]["isError"] is False
        assert execute.call_args.kwargs["config"]["codecompass_capability"] == capability
        assert execute.call_args.kwargs["config"]["codecompass_task_id"] == "test-task"
        assert _rpc(endpoint, "tools/list", token="invalid")[0] == 403
    url = urlsplit(endpoint.url)
    with pytest.raises(OSError):
        socket.create_connection((url.hostname, url.port), timeout=1)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing_task", "codecompass_task_required"),
        ("missing_capability", "retrieval_capability_required"),
        ("expired", "retrieval_capability_expired"),
        ("role_denied", "capability_task_or_operation_not_allowed"),
        ("task_mismatch", "capability_task_or_operation_not_allowed"),
        ("off", "codecompass_access_off"),
        ("authority", "client_authority_forbidden"),
        ("scope", "tool_not_in_allowed_scope"),
    ],
)
def test_wire_call_fails_closed_at_existing_policy_boundary(tmp_path, case, expected):
    capability = (
        None
        if case in {"missing_task", "missing_capability"}
        else _capability(operations=[] if case == "role_denied" else None, expired=case == "expired")
    )
    config = {"codecompass_access": "off"} if case == "off" else {}
    if case == "scope":
        config["allowed_tools"] = ["codecompass.get_file_context"]
    task_id = None if case == "missing_task" else "other-task" if case == "task_mismatch" else "test-task"
    arguments = {"query": "test", **({"capability": "forged"} if case == "authority" else {})}
    with (
        task_capability_scope(capability),
        patch("agent.cli_backends.opencode_codecompass_mcp.get_tool_loop_config", return_value=config),
        opencode_codecompass_mcp(workspace_dir=str(tmp_path), task_id=task_id) as endpoint,
    ):
        status, reply = _rpc(endpoint, "tools/call", {"name": "codecompass.search", "arguments": arguments})
        assert status == 200 and reply["result"]["isError"] is True
        assert json.loads(reply["result"]["content"][0]["text"])["error"] == expected


@pytest.mark.parametrize("reason", ["completed", "timeout", "exception"])
def test_local_provider_merge_and_listener_cleanup_on_every_exit(tmp_path, reason):
    captured = {}
    provider = {"provider": {"ollama": {"models": {"test": {}}}}, "model": "ollama/test"}

    class Runner:
        def run(self, _argv, **kwargs):
            captured.update(kwargs)
            config = json.loads(kwargs["environment"]["OPENCODE_CONFIG_CONTENT"])
            assert config["provider"] == provider["provider"]
            captured["url"] = config["mcp"]["ananta_codecompass"]["url"]
            token = config["mcp"]["ananta_codecompass"]["headers"]["Authorization"][7:]
            assert token in kwargs["secret_values"]
            config_file = os.path.join(kwargs["environment"]["XDG_CONFIG_HOME"], "opencode", "config.json")
            with open(config_file) as handle:
                assert token not in handle.read()
            if reason == "exception":
                raise OSError("injected-process-error")
            return ProcessExecutionResult(0, "ok", "", reason, 3)

    with (
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch(
            "agent.cli_backends.opencode.resolve_opencode_runtime_config", return_value={"provider_config": provider}
        ),
    ):
        result = _run_opencode_subprocess(
            prompt="inspect", model=None, timeout=10, workdir=str(tmp_path), output_format=None, process_runner=Runner()
        )
    assert result[0] == (0 if reason == "completed" else -1)
    assert not os.path.exists(captured["environment"]["XDG_CONFIG_HOME"])
    url = urlsplit(captured["url"])
    with pytest.raises(OSError):
        socket.create_connection((url.hostname, url.port), timeout=1)


def test_installed_opencode_mcp_client_connects_to_standard_endpoint(tmp_path):
    with opencode_codecompass_mcp(workspace_dir=str(tmp_path)) as endpoint:
        result = subprocess.run(
            ["opencode", "mcp", "list"],
            cwd=tmp_path,
            timeout=30,
            capture_output=True,
            text=True,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "XDG_CONFIG_HOME": str(tmp_path),
                "XDG_DATA_HOME": str(tmp_path),
                "XDG_CACHE_HOME": str(tmp_path),
                "OPENCODE_CONFIG_CONTENT": json.dumps({"mcp": {"ananta_codecompass": endpoint.config()}}),
                "NO_COLOR": "1",
                "CI": "1",
            },
        )
    assert result.returncode == 0, result.stderr
    assert "ananta_codecompass" in result.stdout + result.stderr
    assert "connected" in result.stdout + result.stderr


def test_task_bound_native_session_uses_governed_subprocess_and_resumes_session(tmp_path):
    runner = RecordingProcessRunner(ProcessExecutionResult(0, "ok", "", "completed", 3))
    session = {"metadata": {"opencode_runtime": {"kind": "native_server", "native_session_id": "ses_test"}}}
    with (
        task_capability_scope(_capability()),
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch("agent.cli_backends.opencode.check_prompt_budget", return_value=None),
        patch("agent.cli_backends.opencode.prompt_token_limit", return_value=10000),
        patch("agent.cli_backends.opencode.resolve_opencode_runtime_config", return_value={"provider_config": None}),
        patch("agent.cli_backends.opencode._OPENCODE_PROCESS_RUNNER", runner),
        patch(
            "agent.services.opencode_runtime_service.OpencodeRuntimeService.run_session_turn",
            return_value=(0, "bypassed", ""),
        ),
    ):
        assert run_opencode_command("inspect", session=session, workdir=str(tmp_path)) == (0, "ok", "")
    argv, kwargs = runner.calls[0]
    assert argv[-2:] == ("--session", "ses_test")
    assert "ananta_codecompass" in json.loads(kwargs["environment"]["OPENCODE_CONFIG_CONTENT"])["mcp"]


def test_missing_index_is_error_over_wire(tmp_path):
    name = "codecompass.architecture_overview"
    with (
        task_capability_scope(_capability(operations=[f"ananta.tool.{name}"])),
        patch("agent.cli_backends.opencode_codecompass_mcp.get_tool_loop_config", return_value={}),
        patch(
            "agent.services.tools.codecompass_tools._resolve_graph_store_diagnostics",
            return_value=(None, None, [{"code": "index_not_found"}]),
        ),
        opencode_codecompass_mcp(workspace_dir=str(tmp_path)) as endpoint,
    ):
        status, reply = _rpc(endpoint, "tools/call", {"name": name, "arguments": {"query": "architecture"}})
        assert status == 200 and reply["result"]["isError"] is True
        result = json.loads(reply["result"]["content"][0]["text"])
        assert result["error"] == "architecture_graph_unavailable"


def test_hub_gateway_mode_uses_task_binding_without_delegated_capability(tmp_path):
    with (
        task_capability_scope(None, task_id="test-task"),
        patch(
            "agent.cli_backends.opencode_codecompass_mcp.get_tool_loop_config",
            return_value={"codecompass_access": "hub"},
        ),
        patch("agent.config.settings.role", "worker"),
        patch("agent.services.codecompass_hub_tool_client.hub_tool_gateway_client") as gateway,
        opencode_codecompass_mcp(workspace_dir=str(tmp_path)) as endpoint,
    ):
        gateway.return_value.execute.return_value = {"status": "error", "error": "hub_gateway_role_denied"}
        status, reply = _rpc(endpoint, "tools/call", {"name": "codecompass.search", "arguments": {"query": "test"}})
        assert status == 200 and reply["result"]["isError"] is True
        assert gateway.return_value.execute.call_args.kwargs["task_id"] == "test-task"


def test_task_bound_live_terminal_uses_governed_subprocess_without_capability(tmp_path):
    runner = RecordingProcessRunner(ProcessExecutionResult(0, "ok", "", "completed", 3))
    session = {"id": "test-terminal", "metadata": {"opencode_execution_mode": "live_terminal"}}
    with (
        task_capability_scope(None, task_id="test-task"),
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch("agent.cli_backends.opencode.check_prompt_budget", return_value=None),
        patch("agent.cli_backends.opencode.prompt_token_limit", return_value=10000),
        patch("agent.cli_backends.opencode.resolve_opencode_runtime_config", return_value={"provider_config": None}),
        patch("agent.cli_backends.opencode._OPENCODE_PROCESS_RUNNER", runner),
        patch(
            "agent.services.live_terminal_session_service.LiveTerminalSessionService.ensure_session_for_cli",
            return_value={},
        ),
        patch("agent.services.live_terminal_session_service.LiveTerminalSessionService.append_output") as output,
        patch("agent.services.live_terminal_session_service.LiveTerminalSessionService.run_opencode_turn") as legacy,
    ):
        assert run_opencode_command("inspect", session=session, workdir=str(tmp_path)) == (0, "ok", "")
    legacy.assert_not_called()
    output.assert_called()
    assert "ananta_codecompass" in json.loads(runner.calls[0][1]["environment"]["OPENCODE_CONFIG_CONTENT"])["mcp"]
