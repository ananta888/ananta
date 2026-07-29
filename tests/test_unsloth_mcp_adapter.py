from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent.services.opaque_secret_reference_service import OpaqueSecretReferenceService
from agent.services.unsloth_mcp_adapter import (
    UnslothMcpAdapter,
    UnslothMcpError,
    UnslothMcpToolPolicy,
    default_unsloth_mcp_tool_policies,
)
from agent.services.unsloth_studio_worker_adapter import (
    UnslothHubTaskCommand,
    UnslothStudioWorkerAdapter,
)
from agent.services.workflow_runtime.security import InMemoryReplayNonceStore

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "unsloth_studio"
    / "mcp-tools-list.v1.json"
)
_TOKEN = "mcp-test-bearer-token"


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tools_response: Mapping[str, Any] = json.loads(
            _FIXTURE.read_text(encoding="utf-8")
        )
        self.tool_content: Any = {
            "content": [
                {
                    "type": "text",
                    "text": "Bearer leaked-value",
                }
            ],
            "isError": False,
        }

    def request_json(self, **values: Any) -> Mapping[str, Any]:
        self.calls.append(dict(values))
        payload = dict(values.get("payload") or {})
        if payload.get("method") == "initialize":
            response_headers = values.get(
                "response_headers"
            )
            if isinstance(response_headers, dict):
                response_headers["Mcp-Session-Id"] = (
                    "session-test-0001"
                )
            return {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {
                        "name": "Unsloth Studio",
                        "version": "test",
                    },
                },
            }
        if payload.get("method") == "notifications/initialized":
            return {}
        if payload.get("method") == "tools/list":
            return dict(self.tools_response)
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": self.tool_content,
        }


class _HubTaskCommands:
    def __init__(self) -> None:
        self.commands: list[UnslothHubTaskCommand] = []

    def submit(self, command: UnslothHubTaskCommand) -> Mapping[str, Any]:
        self.commands.append(command)
        return {
            "task_id": "task-unsloth-1",
            "authorization": "Bearer must-not-leak",
        }


def _adapter() -> tuple[
    UnslothMcpAdapter,
    _FakeTransport,
    _HubTaskCommands,
    list[tuple[str, Mapping[str, object]]],
]:
    transport = _FakeTransport()
    commands = _HubTaskCommands()
    studio = UnslothStudioWorkerAdapter(
        transport=transport,
        hub_task_commands=commands,
        allowed_mutations=("stop_training",),
    )
    clock = lambda: 100.0
    audits: list[tuple[str, Mapping[str, object]]] = []
    adapter = UnslothMcpAdapter(
        transport=transport,
        studio_adapter=studio,
        replay_store=InMemoryReplayNonceStore(clock=clock),
        mcp_bearer_secret_ref="env://UNSLOTH_MCP_BEARER",
        secret_resolver=OpaqueSecretReferenceService(
            {"UNSLOTH_MCP_BEARER": _TOKEN}
        ),
        tool_policies=default_unsloth_mcp_tool_policies(),
        audit_sink=lambda event, details: audits.append((event, details)),
        clock=clock,
    )
    return adapter, transport, commands, audits


def test_read_tool_requires_successful_probe_and_uses_separate_mcp_bearer() -> None:
    adapter, transport, _commands, audits = _adapter()
    result = adapter.execute(
        tool_id="studio_status",
        arguments={},
        tenant_id="tenant-1",
        actor_id="user-1",
        roles=("viewer",),
        replay_nonce="nonce-studio-status-0001",
        replay_expires_at=200.0,
        correlation_id="correlation-status-0001",
    )
    assert result["content"]["content"][0]["text"] == "Bearer ***"
    assert [call["payload"]["method"] for call in transport.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert all(
        call["service_bearer_secret_ref"]
        == "env://UNSLOTH_MCP_BEARER"
        for call in transport.calls
    )
    assert audits[-1][1]["correlation_id"] == "correlation-status-0001"


def test_stop_training_is_queued_at_hub_and_never_called_through_mcp() -> None:
    adapter, transport, commands, _audits = _adapter()
    result = adapter.execute(
        tool_id="stop_training",
        arguments={"save": True},
        tenant_id="tenant-1",
        actor_id="admin-1",
        roles=("admin",),
        replay_nonce="nonce-stop-training-0001",
        replay_expires_at=200.0,
        correlation_id="correlation-stop-0001",
        confirmation_id="approval-1",
        idempotency_key="idempotency-key-0001",
    )
    assert result["status"] == "queued"
    assert result["receipt"]["authorization"] == "[REDACTED]"
    assert [call["payload"]["method"] for call in transport.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list"
    ]
    assert len(commands.commands) == 1
    assert commands.commands[0].command_type == "unsloth.mcp.stop_training"
    assert commands.commands[0].payload["correlation_id"] == "correlation-stop-0001"


def test_mutation_requires_admin_confirmation_and_idempotency() -> None:
    adapter, _transport, commands, audits = _adapter()
    common = {
        "tool_id": "stop_training",
        "arguments": {},
        "tenant_id": "tenant-1",
        "actor_id": "operator-1",
        "replay_nonce": "nonce-training-policy-01",
        "replay_expires_at": 200.0,
        "correlation_id": "correlation-policy-01",
    }
    with pytest.raises(UnslothMcpError, match="unsloth_mcp_admin_role_required"):
        adapter.execute(roles=("operator",), **common)
    with pytest.raises(UnslothMcpError, match="unsloth_mcp_confirmation_required"):
        adapter.execute(roles=("admin",), **common)
    with pytest.raises(UnslothMcpError, match="unsloth_mcp_idempotency_key_required"):
        adapter.execute(
            roles=("admin",),
            confirmation_id="approval-1",
            **common,
        )
    assert commands.commands == []
    assert all(event == "unsloth_mcp_execution_denied" for event, _ in audits)


def test_replay_nonce_is_consumed_once() -> None:
    adapter, _transport, _commands, _audits = _adapter()
    values = {
        "tool_id": "get_training_status",
        "arguments": {},
        "tenant_id": "tenant-1",
        "actor_id": "user-1",
        "roles": ("viewer",),
        "replay_nonce": "nonce-replay-status-0001",
        "replay_expires_at": 200.0,
        "correlation_id": "correlation-replay-0001",
    }
    adapter.execute(**values)
    with pytest.raises(UnslothMcpError, match="unsloth_mcp_replay_detected"):
        adapter.execute(**values)


def test_mcp_bearer_secret_is_mandatory_and_fail_closed() -> None:
    adapter, _transport, _commands, _audits = _adapter()
    adapter._secret_resolver = OpaqueSecretReferenceService({})
    with pytest.raises(
        UnslothMcpError,
        match="unsloth_mcp_bearer_secret_unavailable",
    ):
        adapter.execute(
            tool_id="studio_status",
            arguments={},
            tenant_id="tenant-1",
            actor_id="user-1",
            roles=("viewer",),
            replay_nonce="nonce-missing-secret-01",
            replay_expires_at=200.0,
            correlation_id="correlation-secret-01",
        )


def test_argument_schemas_reject_path_url_and_shell_surfaces() -> None:
    with pytest.raises(ValueError, match="unsloth_mcp_argument_name_forbidden"):
        UnslothMcpToolPolicy(
            tool_id="unsafe",
            access_class="read",
            allowed_roles=("admin",),
            argument_schema={
                "type": "object",
                "properties": {"model_path": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
        )
    adapter, _transport, _commands, _audits = _adapter()
    with pytest.raises(
        UnslothMcpError,
        match="unsloth_mcp_argument_value_forbidden",
    ):
        adapter.execute(
            tool_id="get_recipe_job_status",
            arguments={"job_id": "../unsafe"},
            tenant_id="tenant-1",
            actor_id="admin-1",
            roles=("admin",),
            replay_nonce="nonce-unsafe-argument-01",
            replay_expires_at=200.0,
            correlation_id="correlation-unsafe-01",
        )


def test_probe_rejects_missing_allowlisted_upstream_tool() -> None:
    adapter, transport, _commands, _audits = _adapter()
    response = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    response["result"]["tools"] = [
        tool
        for tool in response["result"]["tools"]
        if tool.get("name") != "stop_training"
    ]
    transport.tools_response = response
    with pytest.raises(
        UnslothMcpError,
        match="incompatible_upstream_contract",
    ):
        adapter.probe()
