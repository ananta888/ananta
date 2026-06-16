"""VACGE-008: Tests for ConfigGraphPatchService (validation + apply)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agent.services.config_graph_builder_service import ConfigGraphBuilderService, ConfigGraphNode
from agent.services.config_graph_patch_service import (
    RISK_CRITICAL,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    ApplyResult,
    ConfigGraphPatchService,
    PatchOp,
    ValidationResult,
)


def make_graph():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "docs/agent-profiles").mkdir(parents=True)
    (tmp / "AGENTS.md").write_text("# Root\n")
    pm = {
        "profiles": {
            "my_profile": {
                "agents_file": "",
                "primary_role": "planner",
                "activation": [],
                "allowed_task_kinds": ["bugfix"],
            }
        }
    }
    (tmp / "docs/agent-profiles/profile-map.json").write_text(json.dumps(pm))
    return ConfigGraphBuilderService(repo_root=tmp).build()


# ── ValidationResult dataclass ────────────────────────────────────────────────

def test_validation_result_to_dict():
    vr = ValidationResult(valid=True, risk_tier=RISK_LOW)
    d = vr.to_dict()
    assert d["valid"] is True
    assert d["risk_tier"] == RISK_LOW
    assert "errors" in d
    assert "warnings" in d
    assert "requires_approval" in d
    assert "risk_score" in d


# ── PatchOp dataclass ─────────────────────────────────────────────────────────

def test_patch_op_to_dict():
    op = PatchOp(op="set_data", target="some::node", data={"key": "val"})
    d = op.to_dict()
    assert d["op"] == "set_data"
    assert d["target"] == "some::node"
    assert d["data"]["key"] == "val"


# ── Empty patch ───────────────────────────────────────────────────────────────

def test_validate_empty_ops_is_valid_with_warning():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [])
    assert result.valid is True
    assert len(result.warnings) > 0


# ── Unknown op ────────────────────────────────────────────────────────────────

def test_validate_unknown_op_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [PatchOp(op="teleport_node", target="x", data={})])
    assert result.valid is False
    assert result.risk_tier == RISK_CRITICAL


# ── set_data ──────────────────────────────────────────────────────────────────

def test_validate_set_data_on_existing_node():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    node_id = "agent_profile::my_profile"
    result = svc.validate(graph, [PatchOp(op="set_data", target=node_id, data={"x": 1})])
    assert result.valid is True
    assert result.risk_tier in (RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL)


def test_validate_set_data_on_missing_node_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [PatchOp(op="set_data", target="no::such::node", data={})])
    assert result.valid is False


def test_validate_set_data_on_instruction_layer_is_critical():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="set_data", target="instruction_layer::root", data={"content": "hack"})
    ])
    assert result.risk_tier == RISK_CRITICAL


# ── add_edge ──────────────────────────────────────────────────────────────────

def test_validate_add_edge_requires_source_target_type():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [PatchOp(op="add_edge", target="e1", data={})])
    assert result.valid is False


def test_validate_add_edge_valid():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    src = "agent_profile::my_profile"
    tgt = "instruction_layer::root"
    result = svc.validate(graph, [
        PatchOp(op="add_edge", target="e", data={
            "source": src, "target": tgt, "edge_type": "contains",
        })
    ])
    assert result.valid is True
    assert result.risk_tier == RISK_MEDIUM


def test_validate_add_edge_missing_source_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="add_edge", target="e", data={
            "source": "no::such", "target": "instruction_layer::root", "edge_type": "contains",
        })
    ])
    assert result.valid is False


# ── remove_edge ───────────────────────────────────────────────────────────────

def test_validate_remove_edge_not_found_warns():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="remove_edge", target="x", data={
            "source": "a", "target": "b", "edge_type": "contains",
        })
    ])
    assert result.valid is True
    assert len(result.warnings) > 0


# ── remove_node ───────────────────────────────────────────────────────────────

def test_validate_remove_root_instruction_layer_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="remove_node", target="instruction_layer::root", data={})
    ])
    assert result.valid is False
    assert result.risk_tier == RISK_CRITICAL


def test_validate_remove_profile_node_is_high_risk():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="remove_node", target="agent_profile::my_profile", data={})
    ])
    assert result.risk_tier in (RISK_HIGH, RISK_CRITICAL)


def test_validate_remove_missing_node_warns():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="remove_node", target="no::node", data={})
    ])
    assert result.valid is True
    assert len(result.warnings) > 0


# ── add_node ─────────────────────────────────────────────────────────────────

def test_validate_add_node_valid():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [
        PatchOp(op="add_node", target="new_node", data={
            "id": "tool::new_tool", "node_type": "tool", "label": "New Tool",
        })
    ])
    assert result.valid is True
    assert result.risk_tier == RISK_LOW


def test_validate_add_node_missing_id_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    result = svc.validate(graph, [PatchOp(op="add_node", target="", data={})])
    assert result.valid is False


# ── Batch patch validation ────────────────────────────────────────────────────

def test_validate_too_many_ops_fails():
    graph = make_graph()
    svc = ConfigGraphPatchService(max_ops_per_patch=3)
    ops = [PatchOp(op="set_data", target="instruction_layer::root", data={}) for _ in range(5)]
    result = svc.validate(graph, ops)
    assert result.valid is False
    assert result.risk_tier == RISK_CRITICAL


def test_max_risk_aggregated():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    ops = [
        PatchOp(op="add_node", target="x", data={"id": "t::x", "node_type": "tool", "label": "x"}),
        PatchOp(op="remove_node", target="agent_profile::my_profile", data={}),
    ]
    result = svc.validate(graph, ops)
    assert result.risk_tier in (RISK_HIGH, RISK_CRITICAL)


# ── Apply tests ───────────────────────────────────────────────────────────────

def test_apply_set_data():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    node_id = "agent_profile::my_profile"
    ops = [PatchOp(op="set_data", target=node_id, data={"extra": "value"})]
    result = svc.apply(graph, ops, skip_validation=True)
    assert result.success is True
    assert graph.nodes[node_id].data.get("extra") == "value"


def test_apply_add_node():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    ops = [PatchOp(op="add_node", target="tool::alpha", data={
        "id": "tool::alpha", "node_type": "tool", "label": "Alpha Tool",
    })]
    result = svc.apply(graph, ops, skip_validation=True)
    assert result.success is True
    assert "tool::alpha" in graph.nodes


def test_apply_remove_node():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    target = "agent_profile::my_profile"
    ops = [PatchOp(op="remove_node", target=target, data={})]
    result = svc.apply(graph, ops, skip_validation=True)
    assert result.success is True
    assert target not in graph.nodes


def test_apply_removes_incident_edges():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    target = "agent_profile::my_profile"
    initial_edge_count = len(graph.edges)
    ops = [PatchOp(op="remove_node", target=target, data={})]
    svc.apply(graph, ops, skip_validation=True)
    # All edges referencing the removed node should be gone
    for edge in graph.edges:
        assert edge.source != target
        assert edge.target != target


def test_apply_add_edge():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    src = "agent_profile::my_profile"
    tgt = "instruction_layer::root"
    ops = [PatchOp(op="add_edge", target="e", data={
        "source": src, "target": tgt, "edge_type": "contains",
    })]
    initial_count = len(graph.edges)
    result = svc.apply(graph, ops, skip_validation=True)
    assert result.success is True
    assert len(graph.edges) == initial_count + 1


def test_apply_blocked_when_requires_approval():
    graph = make_graph()
    svc = ConfigGraphPatchService(require_approval_above=RISK_MEDIUM)
    # remove_node on a path_rule is high-risk (> medium) → requires approval
    from agent.services.config_graph_builder_service import NODE_PATH_RULE, ConfigGraphNode
    graph.add_node(ConfigGraphNode(
        id="path_rule::src/**", node_type=NODE_PATH_RULE, label="src/**",
        runtime_active=True, data={}
    ))
    ops = [PatchOp(op="remove_node", target="path_rule::src/**", data={})]
    result = svc.apply(graph, ops)
    assert result.success is False
    assert any("approval" in e.lower() for e in result.errors)


def test_apply_approved_succeeds_with_valid_token():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    ops = [PatchOp(op="add_node", target="t", data={
        "id": "tool::beta", "node_type": "tool", "label": "Beta",
    })]
    result = svc.apply_approved(graph, ops, "tok12345")
    assert result.success is True


def test_apply_approved_fails_with_short_token():
    graph = make_graph()
    svc = ConfigGraphPatchService()
    ops = [PatchOp(op="add_node", target="t", data={
        "id": "tool::beta2", "node_type": "tool", "label": "Beta2",
    })]
    result = svc.apply_approved(graph, ops, "short")
    assert result.success is False


def test_apply_updates_snapshot_id():
    graph = make_graph()
    old_sid = graph.snapshot_id
    svc = ConfigGraphPatchService()
    ops = [PatchOp(op="add_node", target="t", data={
        "id": "tool::gamma", "node_type": "tool", "label": "Gamma",
    })]
    result = svc.apply(graph, ops, skip_validation=True)
    assert graph.snapshot_id != old_sid
    assert result.new_snapshot_id == graph.snapshot_id
