from agent.db_models import TaskDB
from agent.repository import audit_repo, task_repo
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
