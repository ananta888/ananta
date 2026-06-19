"""VACGE-008: Tests for ConfigGraphBuilderService."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.services.config_graph_builder_service import (
    GRAPH_SCHEMA,
    ConfigGraph,
    ConfigGraphBuilderService,
    ConfigGraphEdge,
    ConfigGraphNode,
    EDGE_ACTIVATES,
    EDGE_ASSIGNED_TO,
    EDGE_CONTAINS,
    EDGE_INHERITS_FROM,
    EDGE_USES_PROFILE,
    NODE_AGENT_PROFILE,
    NODE_EMBEDDING_MODEL,
    NODE_GOAL_TEMPLATE,
    NODE_INSTRUCTION_LAYER,
    NODE_MODEL_PROVIDER,
    NODE_PATH_RULE,
    NODE_ROLE,
    NODE_SURFACE,
    NODE_TASK_KIND,
    NODE_TOOL,
    NODE_TOOL_GROUP,
    VIEW_IDS,
    VIEW_AGENT_RUNTIME,
    VIEW_CONTEXT_PIPELINE,
    VIEW_CONFIGURATION_OVERVIEW,
    VIEW_EFFECTIVE_CONFIG,
    VIEW_PLANNING_FLOW,
    VIEW_POLICY_PATH,
    VIEW_PROFILE_ACTIVATION,
    get_config_graph_builder_service,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_temp_repo(profile_map: dict | None = None, with_agents_md: bool = True) -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "docs/agent-profiles").mkdir(parents=True)
    (tmp / "agent/services").mkdir(parents=True)

    # Root AGENTS.md
    if with_agents_md:
        (tmp / "AGENTS.md").write_text("# Root Instructions\n")

    # profile-map.json
    if profile_map is not None:
        (tmp / "docs/agent-profiles/profile-map.json").write_text(
            json.dumps(profile_map)
        )
    else:
        default_pm = {
            "schema": "ananta.agent_profile_map.v2",
            "profiles": {
                "test_profile": {
                    "agents_file": "docs/agent-profiles/test_profile_agents.md",
                    "primary_role": "planner",
                    "activation": [{"surface": "test_surface"}],
                    "allowed_task_kinds": ["bugfix", "implementation"],
                    "code_change_policy": "allow_with_review",
                    "context_policy_hint": "full_context",
                }
            },
        }
        (tmp / "docs/agent-profiles/profile-map.json").write_text(
            json.dumps(default_pm)
        )
        (tmp / "docs/agent-profiles/test_profile_agents.md").write_text(
            "# Test Profile Instructions\n"
        )

    return tmp


# ── Schema and metadata tests ─────────────────────────────────────────────────

def test_graph_schema():
    assert GRAPH_SCHEMA == "ananta_configuration_graph.v1"


def test_build_returns_config_graph():
    tmp = make_temp_repo()
    builder = ConfigGraphBuilderService(repo_root=tmp)
    graph = builder.build()
    assert isinstance(graph, ConfigGraph)
    assert graph.schema == GRAPH_SCHEMA


def test_snapshot_id_is_unique():
    tmp = make_temp_repo()
    builder = ConfigGraphBuilderService(repo_root=tmp)
    g1 = builder.build()
    g2 = builder.build()
    assert g1.snapshot_id != g2.snapshot_id


def test_to_dict_has_required_keys():
    tmp = make_temp_repo()
    builder = ConfigGraphBuilderService(repo_root=tmp)
    d = builder.build().to_dict()
    for key in ("schema", "snapshot_id", "nodes", "edges", "views", "diagnostics",
                 "generated_at", "node_count", "edge_count"):
        assert key in d, f"missing key: {key}"


# ── Instruction layer tests ───────────────────────────────────────────────────

def test_root_instruction_layer_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert "instruction_layer::root" in graph.nodes


def test_root_instruction_layer_active_when_file_exists():
    tmp = make_temp_repo(with_agents_md=True)
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert graph.nodes["instruction_layer::root"].runtime_active is True


def test_root_instruction_layer_inactive_when_missing():
    tmp = make_temp_repo(with_agents_md=False)
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    node = graph.nodes["instruction_layer::root"]
    assert node.runtime_active is False
    assert len(node.diagnostics) > 0


def test_profile_instruction_layer_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert "instruction_layer::test_profile" in graph.nodes


def test_profile_layer_inherits_from_root():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    edges = [e for e in graph.edges if e.edge_type == EDGE_INHERITS_FROM]
    assert any(e.source == "instruction_layer::test_profile" and e.target == "instruction_layer::root"
               for e in edges)


# ── Agent profile tests ───────────────────────────────────────────────────────

def test_agent_profile_node_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert "agent_profile::test_profile" in graph.nodes


def test_agent_profile_node_type():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert graph.nodes["agent_profile::test_profile"].node_type == NODE_AGENT_PROFILE


def test_agent_profile_data_fields():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    data = graph.nodes["agent_profile::test_profile"].data
    assert data["profile_id"] == "test_profile"
    assert "bugfix" in data["allowed_task_kinds"]


def test_role_node_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert "role::planner" in graph.nodes


def test_role_assigned_to_edge():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    edges = [e for e in graph.edges if e.edge_type == EDGE_ASSIGNED_TO]
    assert any(
        e.source == "agent_profile::test_profile" and e.target == "role::planner"
        for e in edges
    )


def test_missing_agents_file_adds_diagnostic():
    tmp = make_temp_repo()
    pm = {
        "profiles": {
            "orphan": {
                "agents_file": "docs/agent-profiles/missing.md",
                "primary_role": "worker",
                "activation": [],
                "allowed_task_kinds": [],
            }
        }
    }
    (tmp / "docs/agent-profiles/profile-map.json").write_text(json.dumps(pm))
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    node = graph.nodes.get("agent_profile::orphan")
    assert node is not None
    assert any("not found" in d for d in node.diagnostics)


# ── Surface tests ─────────────────────────────────────────────────────────────

def test_surface_nodes_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    surface_nodes = [n for n in graph.nodes.values() if n.node_type == NODE_SURFACE]
    assert len(surface_nodes) >= 2  # ai_snake_chat and ananta_worker at minimum


def test_surface_in_profile_activation_view():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    view = graph.views.get(VIEW_PROFILE_ACTIVATION, [])
    assert any("surface::" in nid for nid in view)


# ── Path rule tests ───────────────────────────────────────────────────────────

def test_path_rules_added_from_config():
    tmp = make_temp_repo()
    cfg = {
        "path_ai_modes": [
            {"path_glob": "src/security/**", "blocked_ai_modes": ["full_llm"]},
        ]
    }
    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()
    rule_nodes = [n for n in graph.nodes.values() if n.node_type == NODE_PATH_RULE]
    assert len(rule_nodes) == 1
    assert rule_nodes[0].data["path_glob"] == "src/security/**"


def test_no_path_rules_adds_diagnostic():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert any("path_ai_modes" in d for d in graph.diagnostics)


def test_path_rule_in_policy_view():
    tmp = make_temp_repo()
    cfg = {"path_ai_modes": [{"path_glob": "src/**", "blocked_ai_modes": []}]}
    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()
    view = graph.views.get(VIEW_POLICY_PATH, [])
    assert any("path_rule::" in nid for nid in view)


# ── Model tests ───────────────────────────────────────────────────────────────

def test_embedding_model_node_added():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert "embedding_model::default" in graph.nodes


def test_model_provider_node_added():
    tmp = make_temp_repo()
    cfg = {"chat_backend": "lmstudio"}
    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()
    assert "model_provider::lmstudio" in graph.nodes


def test_embedding_model_in_context_pipeline_view():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    view = graph.views.get(VIEW_CONTEXT_PIPELINE, [])
    assert "embedding_model::default" in view


# ── Planning template tests ───────────────────────────────────────────────────

def test_planning_templates_added_from_profile_kinds():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    tmpl_nodes = [n for n in graph.nodes.values() if n.node_type == NODE_GOAL_TEMPLATE]
    assert len(tmpl_nodes) >= 1


def test_stale_template_has_diagnostic():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    stale = [n for n in graph.nodes.values()
             if n.node_type == NODE_GOAL_TEMPLATE and n.stale]
    assert len(stale) >= 1
    for n in stale:
        assert any("stale" in d for d in n.diagnostics)


def test_task_kind_node_links_to_template():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    edges = [e for e in graph.edges if e.edge_type == "uses_template"]
    assert len(edges) >= 1


# ── View tests ────────────────────────────────────────────────────────────────

def test_all_views_exist():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    for view_id in (
        VIEW_CONFIGURATION_OVERVIEW,
        VIEW_PROFILE_ACTIVATION, VIEW_PLANNING_FLOW, VIEW_AGENT_RUNTIME,
        VIEW_POLICY_PATH, VIEW_CONTEXT_PIPELINE, VIEW_EFFECTIVE_CONFIG,
    ):
        assert view_id in graph.views, f"view missing: {view_id}"


def test_configuration_overview_view_contains_all_nodes():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert set(graph.views[VIEW_CONFIGURATION_OVERVIEW]) == set(graph.nodes)


def test_effective_config_view_contains_active_nodes():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    view = graph.views[VIEW_EFFECTIVE_CONFIG]
    assert len(view) >= 1
    for nid in view:
        assert graph.nodes[nid].runtime_active is True


# ── Factory function ──────────────────────────────────────────────────────────

def test_factory_returns_builder():
    svc = get_config_graph_builder_service()
    assert isinstance(svc, ConfigGraphBuilderService)


def test_factory_with_user_config():
    svc = get_config_graph_builder_service(user_config={"backend": "ollama"})
    graph = svc.build()
    assert isinstance(graph, ConfigGraph)


# ── Edge integrity ────────────────────────────────────────────────────────────

def test_all_edges_reference_existing_nodes():
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    node_ids = set(graph.nodes.keys())
    for edge in graph.edges:
        assert edge.source in node_ids, f"edge source not in nodes: {edge.source}"
        assert edge.target in node_ids, f"edge target not in nodes: {edge.target}"


def test_profile_map_missing_graceful():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "AGENTS.md").write_text("# Root\n")
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert any("profile-map.json" in d for d in graph.diagnostics)


def test_build_does_not_raise_without_external_services():
    """No tool registry / planning catalog → diagnostics, not exceptions."""
    tmp = make_temp_repo()
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert graph is not None
