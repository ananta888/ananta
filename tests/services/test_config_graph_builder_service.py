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
    NODE_CODECOMPASS_RANKING,
    NODE_EMBEDDING_MODEL,
    NODE_GOAL_TEMPLATE,
    NODE_INSTRUCTION_LAYER,
    NODE_MODEL_PROVIDER,
    NODE_PATH_RULE,
    NODE_RESTRICTED_INFERENCE_MODEL,
    NODE_RESTRICTED_INFERENCE_ROOT,
    NODE_RESTRICTED_INFERENCE_TASK,
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


def test_snapshot_id_is_unique():
    tmp = make_temp_repo()
    builder = ConfigGraphBuilderService(repo_root=tmp)
    g1 = builder.build()
    g2 = builder.build()
    assert g1.snapshot_id != g2.snapshot_id


# ── Instruction layer tests ───────────────────────────────────────────────────


def test_root_instruction_layer_inactive_when_missing():
    tmp = make_temp_repo(with_agents_md=False)
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    node = graph.nodes["instruction_layer::root"]
    assert node.runtime_active is False
    assert len(node.diagnostics) > 0


# ── Agent profile tests ───────────────────────────────────────────────────────


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


def test_rtipm_nodes_added_from_config():
    tmp = make_temp_repo()
    cfg = {
        "restricted_inference": {
            "enabled": True,
            "models": [
                {
                    "id": "mock-reranker",
                    "engine": "mock",
                    "model": "mock-deterministic-v1",
                    "tasks": ["candidate_rerank"],
                }
            ],
            "tasks": {
                "candidate_rerank": {"enabled": True, "preferred_engine": "mock"}
            },
        },
        "codecompass_ranking": {
            "restricted_inference_rerank_enabled": True,
            "trace_scores": True,
        },
    }

    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()

    assert graph.nodes["restricted_inference::root"].node_type == NODE_RESTRICTED_INFERENCE_ROOT
    assert graph.nodes["restricted_inference_model::mock-reranker"].node_type == NODE_RESTRICTED_INFERENCE_MODEL
    assert graph.nodes["restricted_inference_task::candidate_rerank"].node_type == NODE_RESTRICTED_INFERENCE_TASK
    assert graph.nodes["codecompass_ranking::default"].node_type == NODE_CODECOMPASS_RANKING
    assert graph.nodes["restricted_inference::root"].source_pointer == "/restricted_inference"


def test_path_rule_in_policy_view():
    tmp = make_temp_repo()
    cfg = {"path_ai_modes": [{"path_glob": "src/**", "blocked_ai_modes": []}]}
    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()
    view = graph.views.get(VIEW_POLICY_PATH, [])
    assert any("path_rule::" in nid for nid in view)


# ── Model tests ───────────────────────────────────────────────────────────────


def test_model_provider_node_added():
    tmp = make_temp_repo()
    cfg = {"chat_backend": "lmstudio"}
    graph = ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()
    assert "model_provider::lmstudio" in graph.nodes


# ── Planning template tests ───────────────────────────────────────────────────


# ── View tests ────────────────────────────────────────────────────────────────


# ── Factory function ──────────────────────────────────────────────────────────

def test_factory_returns_builder():
    svc = get_config_graph_builder_service()
    assert isinstance(svc, ConfigGraphBuilderService)


def test_factory_with_user_config():
    svc = get_config_graph_builder_service(user_config={"backend": "ollama"})
    graph = svc.build()
    assert isinstance(graph, ConfigGraph)


# ── Edge integrity ────────────────────────────────────────────────────────────


def test_profile_map_missing_graceful():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "AGENTS.md").write_text("# Root\n")
    graph = ConfigGraphBuilderService(repo_root=tmp).build()
    assert any("profile-map.json" in d for d in graph.diagnostics)


# ── The default graph: one build, every structural expectation ────────────────


@pytest.fixture(scope="module")
def default_graph() -> ConfigGraph:
    """The graph of the default temp repository (with AGENTS.md, one profile, no user config), built once."""
    return ConfigGraphBuilderService(repo_root=make_temp_repo()).build()


def test_default_graph_structure_and_integrity(default_graph: ConfigGraph) -> None:
    graph = default_graph
    assert isinstance(graph, ConfigGraph)
    assert graph.schema == GRAPH_SCHEMA
    data = graph.to_dict()
    for key in ("schema", "snapshot_id", "nodes", "edges", "views", "diagnostics",
                "generated_at", "node_count", "edge_count"):
        assert key in data, f"missing key: {key}"
    # instruction layers: the root is active when AGENTS.md exists; the profile layer inherits from it
    assert "instruction_layer::root" in graph.nodes
    assert graph.nodes["instruction_layer::root"].runtime_active is True
    assert "instruction_layer::test_profile" in graph.nodes
    assert any(
        e.edge_type == EDGE_INHERITS_FROM
        and e.source == "instruction_layer::test_profile" and e.target == "instruction_layer::root"
        for e in graph.edges
    )
    node_ids = set(graph.nodes)
    for edge in graph.edges:
        assert edge.source in node_ids, f"edge source not in nodes: {edge.source}"
        assert edge.target in node_ids, f"edge target not in nodes: {edge.target}"
    # no path_ai_modes configured and no external services: diagnostics, not exceptions
    assert any("path_ai_modes" in d for d in graph.diagnostics)


def test_default_graph_agent_profile_and_role(default_graph: ConfigGraph) -> None:
    graph = default_graph
    profile = graph.nodes["agent_profile::test_profile"]
    assert profile.node_type == NODE_AGENT_PROFILE
    assert profile.data["profile_id"] == "test_profile"
    assert "bugfix" in profile.data["allowed_task_kinds"]
    assert "role::planner" in graph.nodes
    assert any(
        e.edge_type == EDGE_ASSIGNED_TO and e.source == "agent_profile::test_profile" and e.target == "role::planner"
        for e in graph.edges
    )


def test_default_graph_surfaces_models_and_templates(default_graph: ConfigGraph) -> None:
    graph = default_graph
    surfaces = [n for n in graph.nodes.values() if n.node_type == NODE_SURFACE]
    assert len(surfaces) >= 2  # ai_snake_chat and ananta_worker at minimum
    assert "embedding_model::default" in graph.nodes
    templates = [n for n in graph.nodes.values() if n.node_type == NODE_GOAL_TEMPLATE]
    assert len(templates) >= 1
    stale = [n for n in templates if n.stale]
    assert len(stale) >= 1
    for node in stale:
        assert any("stale" in d for d in node.diagnostics)
    assert any(e.edge_type == "uses_template" for e in graph.edges)


def test_default_graph_views(default_graph: ConfigGraph) -> None:
    graph = default_graph
    for view_id in (
        VIEW_CONFIGURATION_OVERVIEW, VIEW_PROFILE_ACTIVATION, VIEW_PLANNING_FLOW, VIEW_AGENT_RUNTIME,
        VIEW_POLICY_PATH, VIEW_CONTEXT_PIPELINE, VIEW_EFFECTIVE_CONFIG,
    ):
        assert view_id in graph.views, f"view missing: {view_id}"
    assert set(graph.views[VIEW_CONFIGURATION_OVERVIEW]) == set(graph.nodes)
    effective = graph.views[VIEW_EFFECTIVE_CONFIG]
    assert len(effective) >= 1
    for node_id in effective:
        assert graph.nodes[node_id].runtime_active is True
    assert any("surface::" in node_id for node_id in graph.views.get(VIEW_PROFILE_ACTIVATION, []))
    assert "embedding_model::default" in graph.views.get(VIEW_CONTEXT_PIPELINE, [])
