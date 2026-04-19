from agent.services.platform_governance_service import PlatformGovernanceService
from agent.tool_capabilities import (
    build_capability_contract,
    resolve_allowed_tools,
    validate_tool_calls_against_contract,
)


def test_capability_contract_applies_overrides_without_mutating_defaults():
    contract = build_capability_contract(
        {
            "assistant_tool_capabilities": {
                "overrides": {
                    "list_agents": {"requires_admin": False},
                    "custom_read": {
                        "category": "read",
                        "requires_admin": False,
                        "mutates_state": False,
                        "description": "Custom read tool",
                    },
                }
            }
        }
    )

    assert contract["list_agents"].requires_admin is False
    assert contract["custom_read"].category == "read"
    assert build_capability_contract()["list_agents"].requires_admin is True


def test_allowed_tools_honor_allowlist_denylist_and_admin_requirement():
    contract = build_capability_contract()
    allowed = resolve_allowed_tools(
        {"llm_tool_allowlist": ["list_agents", "create_team", "missing"], "llm_tool_denylist": ["list_agents"]},
        is_admin=False,
        contract=contract,
    )

    assert allowed == set()

    admin_allowed = resolve_allowed_tools(
        {"llm_tool_allowlist": "*", "llm_tool_denylist": ["delete_team"]},
        is_admin=True,
        contract=contract,
    )
    assert "create_team" in admin_allowed
    assert "delete_team" not in admin_allowed


def test_tool_call_validation_reports_stable_reasons_for_edge_cases():
    contract = build_capability_contract()
    blocked, reasons = validate_tool_calls_against_contract(
        [{"name": ""}, {"name": "unknown"}, {"name": "create_team"}],
        allowed_tools={"create_team"},
        contract=contract,
        is_admin=False,
    )

    assert blocked == ["<missing>", "unknown", "create_team"]
    assert reasons == {
        "<missing>": "missing_tool_name",
        "unknown": "unknown_tool",
        "create_team": "admin_required_for_mutating_tool",
    }


def test_platform_governance_normalizes_terminal_policy_and_network_rules():
    service = PlatformGovernanceService()
    decision = service.evaluate_terminal_access(
        cfg={
            "platform_mode": "admin",
            "terminal_policy": {
                "enabled": True,
                "allow_read": True,
                "allow_interactive": False,
                "allowed_roles": ["ops"],
                "allowed_cidrs": ["10.0.0.0/24", "not-a-cidr"],
                "max_session_seconds": "999999",
            },
        },
        terminal_mode="read",
        is_admin=True,
        roles=["ops"],
        remote_addr="10.0.0.7",
    )

    assert decision.allowed is True
    assert decision.policy["max_session_seconds"] == 86400
    assert service.evaluate_action_pack_access("shell", {"action_packs": {"shell": {"enabled": True}}}) is True
