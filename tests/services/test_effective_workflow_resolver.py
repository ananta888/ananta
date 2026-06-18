from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agent.services.config_graph_builder_service import ConfigGraphBuilderService
from agent.services.effective_workflow_resolver import EffectiveWorkflowResolver, SCHEMA


def make_graph(path_ai_modes: list[dict] | None = None):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "docs/agent-profiles").mkdir(parents=True)
    (tmp / "AGENTS.md").write_text("# Root\n", encoding="utf-8")
    (tmp / "docs/agent-profiles/profile-map.json").write_text(json.dumps({
        "profiles": {
            "ai_snake_chat": {
                "agents_file": "",
                "primary_role": "assistant",
                "activation": [{"surface": "ai_snake_chat"}],
                "allowed_task_kinds": ["bugfix", "review"],
                "code_change_policy": "allow",
                "context_policy_hint": "full_context",
            },
            "worker": {
                "agents_file": "",
                "primary_role": "worker",
                "activation": [],
                "allowed_task_kinds": ["implementation"],
                "code_change_policy": "allow",
            },
        }
    }), encoding="utf-8")
    cfg: dict = {
        "chat_backend": "ananta-worker",
        "opencode_runtime": {"execution_mode": "live_terminal"},
        "hub_worker_routing": {"bugfix": "opencode"},
    }
    if path_ai_modes:
        cfg["path_ai_modes"] = path_ai_modes
    return ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build(), cfg


def sample_blueprints() -> list[dict]:
    return [
        {
            "id": "bp-bugfix",
            "name": "Bugfix Workflow Blueprint",
            "description": "Fix and review defects",
            "base_team_type_name": "implementation_team",
            "is_seed": False,
            "roles": [
                {
                    "id": "role-dev",
                    "name": "Bugfix Developer",
                    "template_id": "tpl-bugfix",
                    "sort_order": 1,
                }
            ],
            "artifacts": [
                {
                    "id": "artifact-plan",
                    "kind": "task",
                    "title": "Bugfix Plan",
                    "sort_order": 1,
                }
            ],
        },
        {
            "id": "bp-research",
            "name": "Research Workflow Blueprint",
            "description": "Investigate questions",
            "base_team_type_name": "analysis_team",
            "is_seed": False,
            "roles": [],
            "artifacts": [],
        },
    ]


def sample_templates() -> list[dict]:
    return [
        {
            "id": "tpl-bugfix",
            "name": "Bugfix Template",
            "description": "Template for bugfix implementation",
        }
    ]


def resolve(**overrides):
    graph, cfg = make_graph(overrides.pop("path_ai_modes", None))
    payload = {
        "graph": graph,
        "user_config": cfg,
        "surface": "ai_snake_chat",
        "task_kind": "bugfix",
        "path": "agent/routes/tasks/goals.py",
        "blueprints": sample_blueprints(),
        "templates": sample_templates(),
    }
    payload.update(overrides)
    return EffectiveWorkflowResolver().resolve(**payload)


def test_resolve_returns_effective_workflow_chain_with_blueprint_and_template() -> None:
    result = resolve()

    assert result["schema"] == SCHEMA
    assert result["status"] in {"ok", "warning"}
    assert result["selected"]["agent_profile"]["profile_id"] == "ai_snake_chat"
    assert result["selected"]["blueprint"]["id"] == "bp-bugfix"
    assert result["selected"]["templates"][0]["id"] == "tpl-bugfix"
    assert any(node["node_type"] == "workflow_request" for node in result["effective_chain"])
    assert result["edit_links"]
    assert result["source_index"]


def test_unknown_surface_is_blocked_instead_of_using_hidden_fallback() -> None:
    result = resolve(surface="unknown_surface_xyz", task_kind=None)

    assert result["status"] == "blocked"
    assert any(item["code"] == "unknown_surface_or_profile" for item in result["blocked"])
    assert result["selected"]["agent_profile"] is None


def test_missing_tool_policy_is_default_deny_warning() -> None:
    result = resolve()

    assert result["selected"]["tools"]["allowed"] == []
    assert result["selected"]["tools"]["missing_policy"] is True
    assert any(item["code"] == "missing_tool_policy_default_deny" for item in result["warnings"])


def test_hub_worker_slice_keeps_hub_control_plane_boundary() -> None:
    result = resolve()
    workers = {
        node_id
        for node_id, node in result["graph"]["nodes"].items()
        if node["node_type"] == "worker_instance"
    }

    assert "hub::ananta" in result["graph"]["nodes"]
    assert not any(
        edge["source"] in workers and edge["target"] in workers
        for edge in result["graph"]["edges"]
    )


def test_options_expose_query_dimensions() -> None:
    graph, cfg = make_graph(path_ai_modes=[
        {"path_glob": "agent/routes/**", "blocked_ai_modes": ["full_llm"]},
    ])

    options = EffectiveWorkflowResolver().options(
        graph=graph,
        user_config=cfg,
        blueprints=sample_blueprints(),
    )

    assert options["schema"] == "ananta.effective_workflow.options.v1"
    assert "ai_snake_chat" in options["surfaces"]
    assert "bugfix" in options["task_kinds"]
    assert "agent/routes/**" in options["path_suggestions"]
    assert "opencode" in options["workers"]


def test_compare_reports_selected_differences() -> None:
    left = resolve()
    right = resolve(surface="unknown_surface_xyz", task_kind=None)

    diff = EffectiveWorkflowResolver().compare(left, right)

    assert diff["schema"] == "ananta.effective_workflow.compare.v1"
    assert diff["status"] == "changed"
    assert any(item["field"] == "agent_profile" for item in diff["differences"])
