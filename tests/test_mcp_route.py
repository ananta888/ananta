from agent.db_models import ArtifactDB, TaskDB
from agent.repository import artifact_repo, audit_repo, task_repo
from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionResult,
)
from agent.services.evolution.registry import get_evolution_provider_registry


class McpEvolutionEngine(EvolutionEngine):
    provider_name = "api-evolution"
    capabilities = [EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(
            provider_name=self.provider_name,
            summary=f"MCP analysis for {context.task_id}",
            proposals=[
                EvolutionProposal(
                    title="Review failed MCP task",
                    description="Create a reviewable proposal from MCP.",
                    risk_level="low",
                )
            ],
        )


def _enable_mcp(app, *, require_admin_for_user_auth=True):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {
                "mcp": {
                    "enabled": True,
                    "allow_agent_auth": True,
                    "allow_user_auth": True,
                    "require_admin_for_user_auth": require_admin_for_user_auth,
                    "emit_audit_events": True,
                }
            },
        }


def _set_operation_policy(app, *, allow_operations=None, allow_groups=None, enforced_transports=None):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "operation_policy": {
                "schema_version": "1.0",
                "enabled": True,
                "revision": 0,
                "enforced_transports": list(enforced_transports or ["mcp.tool", "mcp.resource"]),
                "allow_operations": list(allow_operations or []),
                "deny_operations": [],
                "allow_groups": list(allow_groups or []),
                "deny_groups": [],
                "allowed_auth_sources": ["agent_auth", "user_jwt"],
                "require_admin_for_access_classes": ["admin", "write"],
                "require_approval_for_risks": ["critical", "high"],
                "emit_audit_events": True,
            },
        }


def test_mcp_capabilities_blocked_when_exposure_disabled(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {"mcp": {"enabled": False}},
        }

    res = client.get("/v1/mcp/capabilities", headers=admin_auth_header)
    assert res.status_code == 403
    payload = res.get_json()
    assert payload["error"]["message"] == "forbidden"
    assert (payload["error"].get("data") or {}).get("details") == "mcp_exposure_disabled"


def test_mcp_capabilities_returns_tools_and_resources(client, app, admin_auth_header):
    _enable_mcp(app)

    res = client.get("/v1/mcp/capabilities", headers=admin_auth_header)
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["object"] == "ananta.mcp.capabilities"
    assert (payload.get("features") or {}).get("tools") is True
    assert int((payload.get("counts") or {}).get("tools") or 0) >= 5
    assert (payload.get("adapter_registry") or {}).get("adapter") == "mcp"


def test_mcp_tools_list_and_tools_call_tasks_get(client, app, admin_auth_header):
    _enable_mcp(app)
    task_repo.save(TaskDB(id="mcp-task-1", title="MCP Task", status="todo"))

    tools_res = client.post(
        "/v1/mcp", headers=admin_auth_header, json={"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
    )
    assert tools_res.status_code == 200
    tools_payload = tools_res.get_json()
    tools = (tools_payload.get("result") or {}).get("tools") or []
    assert any(item.get("name") == "tasks.get" for item in tools)

    call_res = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "2",
            "method": "tools/call",
            "params": {"name": "tasks.get", "arguments": {"task_id": "mcp-task-1"}},
        },
    )
    assert call_res.status_code == 200
    call_payload = call_res.get_json()
    content = ((call_payload.get("result") or {}).get("content")) or []
    assert content
    assert (content[0].get("json") or {}).get("id") == "mcp-task-1"


def test_mcp_resources_list_and_read(client, app, admin_auth_header):
    _enable_mcp(app)

    list_res = client.post(
        "/v1/mcp", headers=admin_auth_header, json={"jsonrpc": "2.0", "id": "10", "method": "resources/list"}
    )
    assert list_res.status_code == 200
    list_payload = list_res.get_json()
    resources = (list_payload.get("result") or {}).get("resources") or []
    assert any(item.get("uri") == "ananta://system/health" for item in resources)

    read_res = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={"jsonrpc": "2.0", "id": "11", "method": "resources/read", "params": {"uri": "ananta://system/health"}},
    )
    assert read_res.status_code == 200
    read_payload = read_res.get_json()
    contents = (read_payload.get("result") or {}).get("contents") or []
    assert contents
    assert (contents[0].get("text") or {}).get("status") in {"ok", "healthy", "degraded"}


def test_mcp_artifact_tool_and_resource_hide_system_managed_artifacts(
    client,
    app,
    admin_auth_header,
) -> None:
    _enable_mcp(app)
    public = artifact_repo.save(
        ArtifactDB(id="mcp-public-artifact", artifact_metadata={})
    )
    hidden = artifact_repo.save(
        ArtifactDB(
            id="mcp-hidden-index-payload",
            artifact_metadata={
                "system_artifact_kind": "knowledge_index_job_payload"
            },
        )
    )

    tool_response = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "artifact-tool",
            "method": "tools/call",
            "params": {"name": "artifacts.list", "arguments": {}},
        },
    )
    resource_response = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "artifact-resource",
            "method": "resources/read",
            "params": {"uri": "ananta://artifacts/list"},
        },
    )

    assert tool_response.status_code == 200
    tool_payload = tool_response.get_json()["result"]["content"][0]["json"]
    tool_ids = {item["id"] for item in tool_payload["items"]}
    assert public.id in tool_ids
    assert hidden.id not in tool_ids

    assert resource_response.status_code == 200
    resource_payload = resource_response.get_json()["result"]["contents"][0][
        "text"
    ]
    resource_ids = {item["id"] for item in resource_payload["items"]}
    assert public.id in resource_ids
    assert hidden.id not in resource_ids


def test_mcp_user_auth_requires_admin_when_policy_enabled(client, app):
    from agent.auth import generate_token
    from agent.config import settings

    _enable_mcp(app, require_admin_for_user_auth=True)
    token = generate_token(
        {"sub": "user-1", "role": "user", "mfa_enabled": False}, settings.secret_key, expires_in=3600
    )
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/v1/mcp", headers=headers, json={"jsonrpc": "2.0", "id": "3", "method": "tools/list"})
    assert res.status_code == 403
    payload = res.get_json()
    assert (payload.get("error", {}).get("data") or {}).get("details") == "mcp_admin_required"


def test_mcp_tool_calls_emit_audit_events(client, app, admin_auth_header):
    _enable_mcp(app)

    res = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "4",
            "method": "tools/call",
            "params": {"name": "health.get", "arguments": {"basic": True}},
        },
    )
    assert res.status_code == 200

    logs = audit_repo.get_all(limit=500)
    recent_actions = [entry.action for entry in logs[:30]]
    assert "mcp_tool_called" in recent_actions


def test_mcp_evolution_tools_list_analyze_and_read_proposals(client, app, admin_auth_header):
    _enable_mcp(app)
    _set_operation_policy(app, allow_groups=["mcp.read.v1", "mcp.write.v1"])
    task_repo.save(TaskDB(id="mcp-evolution-task", title="MCP Evolution Task", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(McpEvolutionEngine(), default=True)
    try:
        tools_res = client.post(
            "/v1/mcp",
            headers=admin_auth_header,
            json={"jsonrpc": "2.0", "id": "e1", "method": "tools/list"},
        )
        providers_res = client.post(
            "/v1/mcp",
            headers=admin_auth_header,
            json={
                "jsonrpc": "2.0",
                "id": "e2",
                "method": "tools/call",
                "params": {"name": "evolution.providers.list", "arguments": {}},
            },
        )
        analyze_res = client.post(
            "/v1/mcp",
            headers=admin_auth_header,
            json={
                "jsonrpc": "2.0",
                "id": "e3",
                "method": "tools/call",
                "params": {
                    "name": "evolution.analyze",
                    "arguments": {"task_id": "mcp-evolution-task", "reason": "mcp test"},
                },
            },
        )
        proposals_res = client.post(
            "/v1/mcp",
            headers=admin_auth_header,
            json={
                "jsonrpc": "2.0",
                "id": "e4",
                "method": "tools/call",
                "params": {
                    "name": "evolution.proposals.list",
                    "arguments": {"task_id": "mcp-evolution-task"},
                },
            },
        )
    finally:
        registry.clear()

    tool_names = [item.get("name") for item in ((tools_res.get_json().get("result") or {}).get("tools") or [])]
    assert "evolution.providers.list" in tool_names
    assert "evolution.analyze" in tool_names
    assert "evolution.proposals.list" in tool_names

    providers_payload = providers_res.get_json()["result"]["content"][0]["json"]
    assert providers_payload["providers"][0]["provider_name"] == "api-evolution"

    analyze_payload = analyze_res.get_json()["result"]["content"][0]["json"]
    assert analyze_payload["provider_name"] == "api-evolution"
    assert len(analyze_payload["proposal_ids"]) == 1

    proposals_payload = proposals_res.get_json()["result"]["content"][0]["json"]
    assert proposals_payload["task_id"] == "mcp-evolution-task"
    assert proposals_payload["proposal_count"] == 1


def test_mcp_allowlist_filters_lists_and_blocks_direct_dispatch_without_side_effect(
    client,
    app,
    admin_auth_header,
    monkeypatch,
):
    from agent.services.service_registry import get_core_services

    _enable_mcp(app)
    _set_operation_policy(app, allow_operations=["mcp.tool.health.get"])
    called = []
    with app.app_context():
        registry = get_core_services().mcp_registry_service
    monkeypatch.setattr(registry, "call_tool", lambda **kwargs: called.append(kwargs) or {"unexpected": True})

    listed = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={"jsonrpc": "2.0", "id": "allow-list", "method": "tools/list"},
    )
    names = [item["name"] for item in listed.get_json()["result"]["tools"]]
    assert names == ["health.get"]
    assert listed.get_json()["result"]["tools"][0]["annotations"]["ananta/operationId"] == "mcp.tool.health.get"

    denied = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "direct-deny",
            "method": "tools/call",
            "params": {"name": "tasks.get", "arguments": {"task_id": "secret-task"}},
        },
    )
    assert denied.status_code == 403
    assert denied.get_json()["error"]["message"] == "forbidden"
    assert denied.get_json()["error"]["data"]["details"] == "operation_forbidden"
    assert called == []


def test_mcp_unknown_direct_target_is_forbidden_not_disclosed(client, app, admin_auth_header):
    _enable_mcp(app)
    _set_operation_policy(app, allow_operations=["mcp.tool.health.get"])
    response = client.post(
        "/v1/mcp",
        headers=admin_auth_header,
        json={
            "jsonrpc": "2.0",
            "id": "unknown-deny",
            "method": "tools/call",
            "params": {"name": "unknown.probe", "arguments": {}},
        },
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["message"] == "forbidden"
    assert response.get_json()["error"]["data"]["details"] == "operation_forbidden"


def test_operation_inventory_is_admin_only_and_uses_registry_decisions(client, app, admin_auth_header):
    from agent.auth import generate_token
    from agent.config import settings

    admin_response = client.get("/governance/operations?transport=mcp.tool&access_class=read", headers=admin_auth_header)
    assert admin_response.status_code == 200
    payload = admin_response.get_json()["data"]
    assert payload["schema"] == "ananta.operation_policy_inventory.v1"
    assert payload["items"]
    assert all(item["transport"] == "mcp.tool" and item["access_class"] == "read" for item in payload["items"])

    user_token = generate_token(
        {"sub": "user-operation-reader", "role": "user", "mfa_enabled": False},
        settings.secret_key,
        expires_in=3600,
    )
    user_response = client.get(
        "/governance/operations",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert user_response.status_code == 403


def test_rest_operation_rollout_denies_unlisted_prioritized_route(client, app, admin_auth_header):
    _set_operation_policy(
        app,
        allow_operations=["api.config.get"],
        enforced_transports=["api"],
    )
    response = client.get("/governance/operations", headers=admin_auth_header)
    assert response.status_code == 403
    assert response.get_json()["message"] == "operation_forbidden"


def test_invalid_operation_policy_update_is_atomic(client, app, admin_auth_header):
    with app.app_context():
        before = dict(app.config.get("AGENT_CONFIG") or {})
        before.pop("operation_policy", None)
        app.config["AGENT_CONFIG"] = before
    response = client.post(
        "/config",
        headers=admin_auth_header,
        json={
            "operation_policy": {
                "schema_version": "1.0",
                "enabled": True,
                "revision": 0,
                "expected_revision": 0,
                "enforced_transports": ["mcp.tool"],
                "allow_operations": ["mcp.tool.does_not_exist"],
                "deny_operations": [],
                "allow_groups": [],
                "deny_groups": [],
                "allowed_auth_sources": ["user_jwt"],
            }
        },
    )
    assert response.status_code == 400
    assert response.get_json()["message"] == "operation_policy_operation_unknown"
    with app.app_context():
        assert "operation_policy" not in (app.config.get("AGENT_CONFIG") or {})
