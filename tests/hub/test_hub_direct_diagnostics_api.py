"""HDE-022: diagnostics/API for direct execution and custom tools."""
import pytest

from agent.services.dynamic_tool_registry_service import DynamicToolRegistryService


@pytest.fixture
def dynamic_registry(tmp_path, monkeypatch):
    registry = DynamicToolRegistryService(tmp_path)
    import agent.services.dynamic_tool_registry_service as registry_module

    monkeypatch.setattr(registry_module, "dynamic_tool_registry_service", registry)
    return registry


def _activate(registry, name="custom.count_todos"):
    registry.store_promoted_tool(
        name=name,
        spec={
            "name": name,
            "description": "Count TODOs",
            "risk_class": "read",
            "category": "read_only",
            "execution_plane": "worker_runtime",
            "mutation_declaration": "read_only",
            "argument_schema": {"type": "object", "properties": {}},
            "execution_kind": "command_template",
            "command_template": ["grep", "-rc", "TODO", "."],
        },
        proposal_digest="d1",
        validated_digest="d1",
        validation_report_ref=None,
        approval_status="granted",
    )


def test_config_endpoint_returns_direct_execution_block(client, auth_header):
    response = client.get("/api/diagnostics/hub-direct/config", headers=auth_header)
    assert response.status_code == 200
    assert "hub_direct_execution" in response.get_json()


def test_metrics_endpoint_returns_snapshot_and_recent_decisions(client, auth_header):
    response = client.get("/api/diagnostics/hub-direct/metrics", headers=auth_header)
    assert response.status_code == 200
    payload = response.get_json()
    assert "metrics" in payload and "recent_decisions" in payload
    assert "avoided_llm_call_count" in payload["metrics"]


def test_registry_endpoint_shows_static_and_dynamic(client, auth_header, dynamic_registry):
    _activate(dynamic_registry)
    response = client.get("/api/diagnostics/hub-direct/registry", headers=auth_header)
    assert response.status_code == 200
    payload = response.get_json()
    static_names = {row["name"] for row in payload["static"]["tools"]}
    assert "repo.grep" in static_names
    dynamic_rows = payload["dynamic"]["tools"]
    assert dynamic_rows[0]["name"] == "custom.count_todos"
    # No script bodies / templates for ordinary readers:
    assert "command_template" not in dynamic_rows[0]
    assert "spec" not in dynamic_rows[0]


def test_endpoints_require_auth(client):
    assert client.get("/api/diagnostics/hub-direct/config").status_code == 401
    assert client.get("/api/custom-tools").status_code == 401


def test_promotion_actions_require_admin(client, user_auth_header, dynamic_registry):
    _activate(dynamic_registry)
    for path in (
        "/api/custom-tools/proposals/abc/validate",
        "/api/custom-tools/proposals/abc/request-approval",
        "/api/custom-tools/proposals/abc/activate",
        "/api/custom-tools/custom.count_todos/disable",
        "/api/custom-tools/custom.count_todos/enable",
        "/api/custom-tools/custom.count_todos/rollback",
    ):
        response = client.post(path, headers=user_auth_header, json={})
        assert response.status_code == 403, path


def test_admin_can_disable_and_enable(client, admin_auth_header, dynamic_registry):
    _activate(dynamic_registry)
    response = client.post("/api/custom-tools/custom.count_todos/disable", headers=admin_auth_header)
    assert response.status_code == 200
    assert response.get_json()["status"] == "disabled"
    response = client.post("/api/custom-tools/custom.count_todos/enable", headers=admin_auth_header)
    assert response.status_code == 200
    assert response.get_json()["status"] == "active"


def test_proposal_creation_is_open_but_inert(client, user_auth_header, tmp_path, monkeypatch, dynamic_registry):
    from agent.services.custom_tool_proposal_service import CustomToolProposalService
    import agent.services.custom_tool_proposal_service as proposal_module

    monkeypatch.setattr(proposal_module, "custom_tool_proposal_service", CustomToolProposalService(tmp_path / "p"))
    payload = {
        "name": "custom.zaehle",
        "description": "x",
        "proposed_by": "user:test",
        "source_task_id": "t",
        "risk_class": "read",
        "category": "read_only",
        "execution_plane": "worker_runtime",
        "mutation_declaration": "read_only",
        "argument_schema": {"type": "object", "properties": {}},
        "execution_kind": "command_template",
        "command_template": ["true"],
        "timeout_seconds": 5,
        "output_max_chars": 100,
        "tests": [
            {"name": "p", "kind": "positive", "arguments": {}},
            {"name": "n", "kind": "negative", "arguments": {}},
        ],
    }
    response = client.post("/api/custom-tools/proposals", headers=user_auth_header, json=payload)
    assert response.status_code == 201
    assert response.get_json()["status"] == "pending"
    # Inert: tool is not active anywhere.
    assert dynamic_registry.get_active_tool("custom.zaehle") is None


def test_invalid_proposal_returns_400(client, user_auth_header, tmp_path, monkeypatch):
    from agent.services.custom_tool_proposal_service import CustomToolProposalService
    import agent.services.custom_tool_proposal_service as proposal_module

    monkeypatch.setattr(proposal_module, "custom_tool_proposal_service", CustomToolProposalService(tmp_path / "p"))
    response = client.post(
        "/api/custom-tools/proposals", headers=user_auth_header, json={"name": "evil"}
    )
    assert response.status_code == 400
