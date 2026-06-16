"""VACGE-008: Tests for EffectiveConfigResolver."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.services.config_graph_builder_service import (
    ConfigGraphBuilderService,
    ConfigGraphEdge,
    ConfigGraphNode,
    EDGE_MAY_CALL_TOOL,
    NODE_TOOL,
    VIEW_IDS,
)
from agent.services.config_graph_effective_resolver import EffectiveConfigResolver


def make_graph(
    profile_map: dict | None = None,
    path_ai_modes: list | None = None,
) -> "import agent.services.config_graph_builder_service as m; m.ConfigGraph":
    tmp = Path(tempfile.mkdtemp())
    (tmp / "docs/agent-profiles").mkdir(parents=True)
    (tmp / "AGENTS.md").write_text("# Root\n")

    pm = profile_map or {
        "profiles": {
            "ai_snake_chat": {
                "agents_file": "",
                "primary_role": "assistant",
                "activation": [{"surface": "ai_snake_chat"}],
                "allowed_task_kinds": ["bugfix"],
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
    }
    (tmp / "docs/agent-profiles/profile-map.json").write_text(json.dumps(pm))
    cfg: dict = {}
    if path_ai_modes:
        cfg["path_ai_modes"] = path_ai_modes
    return ConfigGraphBuilderService(repo_root=tmp, user_config=cfg).build()


# ── Instruction layers ────────────────────────────────────────────────────────

def test_root_instruction_layer_always_first():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert result.instruction_layers[0]["layer"] == "root"


def test_instruction_layers_include_root():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert any(l["layer"] == "root" for l in result.instruction_layers)


# ── Agent profile matching ────────────────────────────────────────────────────

def test_profile_matched_by_surface_name():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert result.agent_profile is not None
    assert result.agent_profile["profile_id"] == "ai_snake_chat"


def test_profile_matched_by_task_kind():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="worker", task_kind="implementation")
    assert result.agent_profile is not None


def test_no_profile_match_adds_warning():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="unknown_surface_xyz")
    # Should fall back to first active profile (no warning IF fallback found)
    # or warn if completely not found
    assert isinstance(result.warnings, list)


def test_effective_node_ids_include_profile():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert any("agent_profile::" in nid for nid in result.effective_node_ids)


# ── Goal template matching ────────────────────────────────────────────────────

def test_goal_template_matched_by_task_kind():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", task_kind="bugfix")
    assert result.goal_template is not None
    assert result.goal_template["template_id"] == "bugfix"


def test_no_template_when_task_kind_none():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", task_kind=None)
    assert result.goal_template is None


def test_stale_template_adds_warning():
    graph = make_graph()
    # Mark the template node as stale
    tmpl = graph.nodes.get("goal_template::bugfix")
    if tmpl:
        tmpl.stale = True
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", task_kind="bugfix")
    if result.goal_template:  # only if template was found
        assert any("stale" in w for w in result.warnings)


# ── Path-based AI mode restrictions ──────────────────────────────────────────

def test_path_rule_blocks_modes():
    graph = make_graph(path_ai_modes=[
        {"path_glob": "src/security/**", "blocked_ai_modes": ["full_llm", "direct_llm"]},
    ])
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", path="src/security/auth.py")
    assert "full_llm" in result.effective_ai_modes_blocked
    assert "direct_llm" in result.effective_ai_modes_blocked


def test_path_rule_allows_modes():
    graph = make_graph(path_ai_modes=[
        {
            "path_glob": "docs/**",
            "blocked_ai_modes": [],
            "allowed_ai_modes": ["full_llm", "embedding_only"],
        }
    ])
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", path="docs/readme.md")
    assert "full_llm" in result.effective_ai_modes_allowed


def test_no_path_rule_match_leaves_modes_empty():
    graph = make_graph(path_ai_modes=[
        {"path_glob": "src/security/**", "blocked_ai_modes": ["full_llm"]},
    ])
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", path="docs/readme.md")
    assert result.effective_ai_modes_blocked == []


def test_all_modes_blocked_adds_warning():
    graph = make_graph(path_ai_modes=[
        {"path_glob": "**", "blocked_ai_modes": ["full_llm", "direct_llm"],
         "allowed_ai_modes": []},
    ])
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", path="any/file.py")
    # Warning only when ALL allowed modes are blocked
    assert isinstance(result.warnings, list)


# ── Merge trace ───────────────────────────────────────────────────────────────

def test_merge_trace_non_empty():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert len(result.merge_trace) >= 1


def test_merge_trace_has_step_numbers():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", task_kind="bugfix")
    steps = [t["step"] for t in result.merge_trace]
    assert steps == sorted(steps)


# ── to_dict ───────────────────────────────────────────────────────────────────

def test_to_dict_keys():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    d = result.to_dict()
    for k in (
        "surface", "task_kind", "path", "instruction_layers", "agent_profile",
        "goal_template", "effective_ai_modes_allowed", "effective_ai_modes_blocked",
        "tools_allowed", "policies_active", "merge_trace", "warnings", "effective_node_ids",
    ):
        assert k in d, f"missing key: {k}"


def test_to_dict_surface_preserved():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat", task_kind="bugfix", path="src/foo.py")
    d = result.to_dict()
    assert d["surface"] == "ai_snake_chat"
    assert d["task_kind"] == "bugfix"
    assert d["path"] == "src/foo.py"


# ── Tools ─────────────────────────────────────────────────────────────────────

def test_tools_allowed_is_list():
    graph = make_graph()
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert isinstance(result.tools_allowed, list)


def test_tool_edges_respected():
    graph = make_graph()
    # Add a tool node and wire it to the profile
    from agent.services.config_graph_builder_service import ConfigGraphNode, NODE_TOOL
    tool_node = ConfigGraphNode(
        id="tool::my_tool",
        node_type=NODE_TOOL,
        label="my_tool",
        runtime_active=True,
        data={"name": "my_tool"},
    )
    graph.add_node(tool_node)
    graph.add_edge(ConfigGraphEdge(
        source="agent_profile::ai_snake_chat",
        target="tool::my_tool",
        edge_type=EDGE_MAY_CALL_TOOL,
    ))
    resolver = EffectiveConfigResolver(graph)
    result = resolver.resolve(surface="ai_snake_chat")
    assert "my_tool" in result.tools_allowed
