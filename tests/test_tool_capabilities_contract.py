from agent.tool_capabilities import (
    build_capability_contract,
    resolve_allowed_tools,
    validate_tool_calls_against_contract,
)


def test_capability_contract_blocks_unknown_and_denied_tools():
    cfg = {"llm_tool_allowlist": ["create_team"], "llm_tool_denylist": ["create_team"]}
    contract = build_capability_contract(cfg)
    allowed = resolve_allowed_tools(cfg, is_admin=True, contract=contract)
    blocked, reasons = validate_tool_calls_against_contract(
        [{"name": "create_team", "args": {}}, {"name": "non_existing_tool", "args": {}}],
        allowed_tools=allowed,
        contract=contract,
        is_admin=True,
    )
    assert "create_team" in blocked
    assert reasons["create_team"] == "tool_not_allowed_by_capability_contract"
    assert "non_existing_tool" in blocked
    assert reasons["non_existing_tool"] == "unknown_tool"


def test_capability_contract_requires_admin_for_mutating_tool():
    cfg = {"llm_tool_allowlist": ["create_team"]}
    contract = build_capability_contract(cfg)
    allowed_non_admin = resolve_allowed_tools(cfg, is_admin=False, contract=contract)
    blocked, reasons = validate_tool_calls_against_contract(
        [{"name": "create_team", "args": {"name": "A", "team_type": "Scrum"}}],
        allowed_tools=allowed_non_admin,
        contract=contract,
        is_admin=False,
    )
    assert blocked == ["create_team"]
    assert reasons["create_team"] == "admin_required_for_mutating_tool"


def test_llm_generate_exposes_assistant_capabilities_metadata(client, app):
    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "llm_config": {"provider": "ollama", "base_url": "http://localhost:11434/api/generate", "model": "m1"},
            "llm_tool_allowlist": ["list_teams", "create_team"],
        }

    res = client.post(
        "/llm/generate",
        json={"prompt": "hello"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert res.status_code == 200
    data = res.json.get("data") or {}
    caps = data.get("assistant_capabilities") or {}
    assert isinstance(caps.get("tools"), list)
    assert "allowed_tools" in caps
    assert "is_admin" in caps


def test_assistant_read_model_endpoint_returns_aggregated_data(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            "openai_api_key": "abc",
            "default_provider": "lmstudio",
        }

    res = client.get("/assistant/read-model", headers={"Authorization": "Bearer secret-token"})
    assert res.status_code == 200
    data = res.json.get("data") or {}
    assert "config" in data
    assert "teams" in data
    assert "roles" in data
    assert "templates" in data
    assert "agents" in data
    assert "settings" in data
    assert "automation" in data
    assert "assistant_capabilities" in data
    settings = data.get("settings") or {}
    assert isinstance(settings.get("editable_inventory"), list)
    assert settings.get("editable_count", 0) >= 1
    assert (data.get("config") or {}).get("effective", {}).get("openai_api_key") == "***"


def test_capability_contract_blocks_new_admin_tool_for_non_admin():
    cfg = {"llm_tool_allowlist": ["set_autopilot_state"]}
    contract = build_capability_contract(cfg)
    allowed_non_admin = resolve_allowed_tools(cfg, is_admin=False, contract=contract)
    blocked, reasons = validate_tool_calls_against_contract(
        [{"name": "set_autopilot_state", "args": {"action": "stop"}}],
        allowed_tools=allowed_non_admin,
        contract=contract,
        is_admin=False,
    )
    assert blocked == ["set_autopilot_state"]
    assert reasons["set_autopilot_state"] == "admin_required_for_mutating_tool"
