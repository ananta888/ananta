from __future__ import annotations

from agent.routes.tasks.auto_planner import AutoPlanner


def test_plan_goal_returns_validated_plan_proposal_payload(app):
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        result = planner.plan_goal(
            "Implement a small Python Fibonacci helper, add unit tests, and provide a short summary.",
            create_tasks=False,
            use_template=True,
            use_repo_context=False,
        )

    assert result.get("error") is None
    assert isinstance(result.get("plan_proposal"), dict)
    assert result["plan_proposal"]["plan_proposal_contract_version"] == "v1"
    assert result.get("proposal_validation_errors") == []


def test_plan_goal_reports_delegated_planning_fallback_without_planner_worker(app):
    planner = AutoPlanner()
    planner.configure(auto_start_autopilot=False)

    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "planning_policy": {
                "delegated_planning_enabled": True,
                "allowed_planner_roles": ["planning-agent"],
                "allow_remote_planners": False,
            },
        }
        result = planner.plan_goal(
            "Fix critical bug in auth flow",
            create_tasks=False,
            use_template=True,
            use_repo_context=False,
        )

    assert result.get("error") is None
    planner_selection = result.get("planner_selection") or {}
    assert planner_selection.get("delegated_planning_enabled") is True
    assert planner_selection.get("selection_reason") in {
        "no_planning_agent_available_fallback_to_hub",
        "hub_local_planning",
    }
