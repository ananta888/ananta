from agent.services.platform_governance_service import get_platform_governance_service


def test_platform_governance_defaults_terminal_fail_closed():
    service = get_platform_governance_service()

    policy = service.build_policy_read_model({})
    decision = service.evaluate_terminal_access(cfg={}, terminal_mode="interactive", is_admin=True)

    assert policy["platform_mode"] == "local-dev"
    assert policy["terminal_policy"]["enabled"] is False
    assert decision.allowed is False
    assert decision.reason == "terminal_disabled"


def test_platform_governance_merges_explicit_terminal_admin_policy():
    service = get_platform_governance_service()
    cfg = {
        "platform_mode": "admin-only",
        "terminal_policy": {
            "enabled": True,
            "allow_read": True,
            "allow_interactive": False,
            "require_admin": True,
        },
    }

    admin_read = service.evaluate_terminal_access(cfg=cfg, terminal_mode="read", is_admin=True)
    admin_interactive = service.evaluate_terminal_access(cfg=cfg, terminal_mode="interactive", is_admin=True)
    user_read = service.evaluate_terminal_access(cfg=cfg, terminal_mode="read", is_admin=False)

    assert admin_read.allowed is True
    assert admin_interactive.allowed is False
    assert admin_interactive.reason == "terminal_interactive_disabled"
    assert user_read.allowed is False
    assert user_read.reason == "terminal_admin_required"


def test_platform_governance_semi_public_limits_external_exposure():
    service = get_platform_governance_service()

    policy = service.build_policy_read_model({"platform_mode": "semi-public"})
    openai_compat = policy["exposure_policy"]["openai_compat"]
    mcp = policy["exposure_policy"]["mcp"]

    assert openai_compat["enabled"] is True
    assert openai_compat["allow_agent_auth"] is False
    assert openai_compat["allow_files_api"] is False
    assert openai_compat["max_hops"] == 1
    assert mcp["enabled"] is False
    assert policy["exposure_policy"]["remote_hubs"]["enabled"] is False


def test_platform_governance_mode_defaults_override_legacy_base_exposure_defaults():
    service = get_platform_governance_service()
    legacy_default_exposure = {
        "openai_compat": {
            "enabled": True,
            "allow_agent_auth": True,
            "allow_user_auth": True,
            "require_admin_for_user_auth": True,
            "allow_files_api": True,
            "emit_audit_events": True,
            "instance_id": None,
            "max_hops": 3,
        },
            "mcp": {
                "enabled": False,
                "allow_agent_auth": False,
                "allow_user_auth": False,
                "require_admin_for_user_auth": True,
                "emit_audit_events": True,
            },
            "remote_hubs": {
                "enabled": True,
                "require_admin_for_user_auth": True,
                "emit_audit_events": True,
                "max_hops": 3,
            },
        }

    policy = service.build_policy_read_model(
        {
            "platform_mode": "semi-public",
            "exposure_policy": legacy_default_exposure,
        }
    )

    assert policy["exposure_policy"]["openai_compat"]["allow_agent_auth"] is False
    assert policy["exposure_policy"]["openai_compat"]["allow_files_api"] is False
    assert policy["exposure_policy"]["openai_compat"]["max_hops"] == 1
    assert policy["exposure_policy"]["remote_hubs"]["enabled"] is False
