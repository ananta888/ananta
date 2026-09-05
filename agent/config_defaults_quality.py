"""Quality and approval defaults kept separate from base runtime assembly."""

from __future__ import annotations


def generated_source_line_policy_defaults() -> dict:
    common_follow_up = {
        "cross_hard_action": "require_followup",
        "existing_over_hard_growth_action": "require_followup",
        "existing_over_hard_shrink_action": "warn",
        "warn_action": "warn",
    }
    common_warn = {
        "new_over_hard_action": "warn",
        "cross_hard_action": "warn",
        "existing_over_hard_growth_action": "warn",
        "existing_over_hard_shrink_action": "warn",
        "warn_action": "warn",
    }
    return {
        "enabled": False,
        "mode": "warn",
        "create_followup_todo": False,
        "max_report_files": 50,
        "categories": {
            "production_source": {
                "target_lines": 800,
                "warn_after_lines": 600,
                "hard_max_lines": 1000,
                "new_over_hard_action": "block",
                **common_follow_up,
            },
            "facade_or_routes": {
                "target_lines": 900,
                "warn_after_lines": 700,
                "hard_max_lines": 1000,
                "new_over_hard_action": "require_followup",
                **common_follow_up,
            },
            "tests": {
                "target_lines": 1000,
                "warn_after_lines": 1000,
                "hard_max_lines": 1500,
                "new_over_hard_action": "warn",
                **common_follow_up,
            },
            "data_schema_config": {
                "target_lines": 1200,
                "warn_after_lines": 1200,
                "hard_max_lines": 1500,
                **common_warn,
            },
            "generated": {
                "target_lines": 1200,
                "warn_after_lines": 1200,
                "hard_max_lines": 1500,
                **common_warn,
            },
        },
    }


def approval_lifecycle_defaults() -> dict:
    return {
        "enabled": False,
        "legacy_approval_confirmed_enabled": True,
        "default_ttl_seconds": 3600,
        "grant_one_shot": True,
        "auto_approval_policy": {
            "safe": {
                "read_only": True,
                "controlled_workspace_writes": False,
                "test_run": False,
                "recovery_plan_materialization": False,
            },
            "balanced": {
                "read_only": True,
                "controlled_workspace_writes": True,
                "test_run": True,
                "recovery_plan_materialization": False,
            },
            "strict": {
                "read_only": True,
                "controlled_workspace_writes": False,
                "test_run": False,
                "recovery_plan_materialization": False,
            },
        },
        "human_required_tools": [
            "git.push",
            "git.commit",
            "secret.read",
            "service.restart",
            "network.fetch_arbitrary",
            "shell.run_unrestricted",
            "external_worker.execute_mutation",
        ],
        "goal_pre_approvals": {
            "enabled": False,
            "tools": ["test.run"],
            "ttl_seconds": 7200,
        },
    }
