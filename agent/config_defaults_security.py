"""Default security and governance policy blocks of the agent configuration.

Each function returns a fresh dict for one top-level key of
``agent.config_defaults.build_default_agent_config``.
"""


def llm_tool_guardrails_defaults() -> dict:
    return {
        "enabled": True,
        "max_tool_calls_per_request": 10,
        "max_external_calls_per_request": 6,
        "max_estimated_cost_units_per_request": 40,
        "max_tokens_per_request": 6000,
        # the cap grows to the context window (32k): long-context steps are sized for it
        "max_tokens_follow_context_window": True,
        "chars_per_token_estimate": 4,
        "class_limits": {"read": 8, "write": 6, "admin": 1},
        "class_cost_units": {"read": 1, "write": 5, "admin": 8, "unknown": 3},
        "external_classes": ["write", "admin"],
        "tool_classes": {
            # hub/orchestration tools
            "list_teams": "read",
            "list_roles": "read",
            "list_agents": "read",
            "list_templates": "read",
            "analyze_logs": "read",
            "read_agent_logs": "read",
            "create_team": "write",
            "assign_role": "write",
            "ensure_team_templates": "write",
            "create_template": "write",
            "update_template": "write",
            "delete_template": "write",
            "upsert_team_type": "write",
            "delete_team_type": "write",
            "upsert_role": "write",
            "delete_role": "write",
            "link_role_to_team_type": "write",
            "unlink_role_from_team_type": "write",
            "set_role_template_mapping": "write",
            "upsert_team": "write",
            "delete_team": "write",
            "activate_team": "write",
            "configure_auto_planner": "admin",
            "configure_triggers": "admin",
            "set_autopilot_state": "admin",
            "update_config": "admin",
            # worker file/shell tools
            "file_read": "read",
            "file_list": "read",
            "file_write": "write",
            "file_patch": "write",
            "shell_execute": "write",
            "git_status": "read",
            "git_diff": "read",
            "git_log": "read",
            "git_commit": "write",
            "web_fetch": "read",
            "web_search": "read",
            "doc_extract": "read",
        },
    }


def exposure_policy_defaults() -> dict:
    return {
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
        "voice": {
            "enabled": True,
            "allow_agent_auth": False,
            "allow_user_auth": True,
            "require_admin_for_user_auth": False,
            "require_explicit_approval_for_goal": True,
            "emit_audit_events": True,
        },
    }


def terminal_policy_defaults() -> dict:
    return {
        "enabled": False,
        "allow_read": False,
        "allow_interactive": False,
        "require_authenticated": False,
        "require_admin": True,
        "require_admin_for_interactive": True,
        "emit_audit_events": True,
        "max_session_seconds": 1800,
        "idle_timeout_seconds": 300,
        "input_preview_max_chars": 120,
        "allowed_roles": [],
        "allowed_cidrs": [],
    }


def execution_risk_policy_defaults() -> dict:
    return {
        "enabled": True,
        "default_action": "deny",
        "deny_risk_levels": ["critical"],
        "review_risk_levels": ["high", "critical"],
        "task_scoped_only": True,
        "require_terminal_capability_for_command": False,
        "terminal_capability_name": "terminal",
    }


def shell_command_policy_defaults() -> dict:
    return {
        "enabled": True,
        "allow_chain_operators": [";", "&&", "||"],
        "deny_operators": ["|", "`", "$(", "${", "<<"],
        "validate_segments_individually": True,
        "allow_quoted_operators": True,
        "allow_inline_language_code": {"python -c": True, "node -e": True},
        "allow_complex_shell_mode": False,
    }


def autopilot_security_policies_defaults() -> dict:
    return {
        "safe": {
            "max_concurrency_cap": 1,
            "execute_timeout": 45,
            "execute_retries": 0,
            "allowed_tool_classes": ["read"],
        },
        "balanced": {
            "max_concurrency_cap": 4,
            "execute_timeout": 60,
            "execute_retries": 1,
            "allowed_tool_classes": ["read", "write"],
        },
        "aggressive": {
            "max_concurrency_cap": 4,
            "execute_timeout": 120,
            "execute_retries": 2,
            "allowed_tool_classes": ["read", "write", "admin", "unknown"],
        },
    }
