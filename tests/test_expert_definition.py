"""Tests for ExpertDefinition and ExpertRegistry — COSMOS-001"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.services.expert_definition import ExpertDefinition, ExpertRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_expert(**overrides) -> ExpertDefinition:
    defaults = dict(
        expert_id="test_expert",
        version="1.0",
        title="Test Expert",
        purpose="Für Tests",
        allowed_tools=["read_file", "plan_context"],
        denied_tools=["shell_exec", "apply_diff"],
        allowed_path_patterns=["src/**", "tests/**"],
        denied_path_patterns=[".env", ".git/**", "secrets/**"],
        model_routing={"prefer_role": "planner", "cost_class": ["free"]},
        context_strategy="full_domain_map",
        output_contract="dispatch_plan",
        approval_gates=["submit_plan"],
        min_policy_scope="project",
        extends=None,
    )
    defaults.update(overrides)
    return ExpertDefinition(**defaults)


# ── Tool access ───────────────────────────────────────────────────────────────

def test_is_tool_allowed_in_allowed_list():
    expert = _make_expert(allowed_tools=["read_file", "plan_context"], denied_tools=[])
    assert expert.is_tool_allowed("read_file") is True


def test_is_tool_denied_in_denied_list():
    """denied_tools overrides allowed_tools."""
    expert = _make_expert(
        allowed_tools=["read_file", "shell_exec"],
        denied_tools=["shell_exec"],
    )
    assert expert.is_tool_allowed("shell_exec") is False


def test_is_tool_not_in_allowed_returns_false():
    expert = _make_expert(allowed_tools=["read_file"], denied_tools=[])
    assert expert.is_tool_allowed("unknown_tool") is False


# ── Path access ───────────────────────────────────────────────────────────────

def test_is_path_allowed_pattern_match():
    expert = _make_expert(
        allowed_path_patterns=["src/**"],
        denied_path_patterns=[],
    )
    assert expert.is_path_allowed("src/foo/bar.py") is True


def test_denied_path_overrides_allowed():
    """.env must be blocked even if allowed_path_patterns is ['**']."""
    expert = _make_expert(
        allowed_path_patterns=["**"],
        denied_path_patterns=[".env"],
    )
    assert expert.is_path_allowed(".env") is False


def test_path_not_in_allowed_default_deny():
    expert = _make_expert(
        allowed_path_patterns=["src/**"],
        denied_path_patterns=[],
    )
    assert expert.is_path_allowed("config/secret.json") is False


def test_denied_git_path():
    expert = _make_expert(
        allowed_path_patterns=["**"],
        denied_path_patterns=[".git/**"],
    )
    assert expert.is_path_allowed(".git/config") is False


# ── Validation ────────────────────────────────────────────────────────────────

def test_validate_missing_expert_id():
    expert = _make_expert(expert_id="")
    errors = expert.validate()
    assert any("expert_id" in e for e in errors)


def test_validate_valid_expert_returns_no_errors():
    expert = _make_expert()
    errors = expert.validate()
    assert errors == []


def test_validate_invalid_min_policy_scope():
    expert = _make_expert(min_policy_scope="unknown_scope")
    errors = expert.validate()
    assert any("min_policy_scope" in e for e in errors)


# ── Policy intersection ───────────────────────────────────────────────────────

def test_policy_intersection_limits_tools():
    """Hub allows only ['read_file'] — expert must be limited to that."""
    expert = _make_expert(
        allowed_tools=["read_file", "plan_context", "search_symbols"],
        denied_tools=[],
    )
    registry = ExpertRegistry.__new__(ExpertRegistry)
    registry._experts = {}
    result = registry.apply_policy_intersection(
        expert,
        allowed_tools=["read_file"],
        allowed_paths=["**"],
    )
    assert result.allowed_tools == ["read_file"]


def test_policy_intersection_limits_paths():
    """Hub allows only src/** — expert's tests/** pattern should be dropped."""
    expert = _make_expert(
        allowed_path_patterns=["src/**", "tests/**"],
    )
    registry = ExpertRegistry.__new__(ExpertRegistry)
    registry._experts = {}
    result = registry.apply_policy_intersection(
        expert,
        allowed_tools=[],
        allowed_paths=["src/**"],
    )
    assert "tests/**" not in result.allowed_path_patterns
    assert "src/**" in result.allowed_path_patterns


def test_policy_intersection_wildcard_hub_allows_all_expert_paths():
    """Hub with '**' should allow all expert path patterns."""
    expert = _make_expert(
        allowed_path_patterns=["src/**", "tests/**", "docs/**"],
    )
    registry = ExpertRegistry.__new__(ExpertRegistry)
    registry._experts = {}
    result = registry.apply_policy_intersection(
        expert,
        allowed_tools=[],
        allowed_paths=["**"],
    )
    assert set(result.allowed_path_patterns) == {"src/**", "tests/**", "docs/**"}


# ── YAML parsing ──────────────────────────────────────────────────────────────

def test_load_from_yaml_string(tmp_path: Path):
    yaml_content = textwrap.dedent("""\
        expert_id: yaml_expert
        version: "1.0"
        title: YAML Expert
        purpose: "Aus YAML geladen"
        allowed_tools:
          - read_file
        denied_tools:
          - shell_exec
        allowed_path_patterns:
          - "src/**"
        denied_path_patterns:
          - ".env"
        model_routing:
          prefer_role: planner
          cost_class: [free]
        context_strategy: full_domain_map
        output_contract: dispatch_plan
        approval_gates:
          - submit_plan
        min_policy_scope: project
    """)
    yaml_file = tmp_path / "yaml_expert.yaml"
    yaml_file.write_text(yaml_content)

    registry = ExpertRegistry(config_dir=tmp_path)
    experts = registry.load_all()

    assert "yaml_expert" in experts
    expert = experts["yaml_expert"]
    assert expert.expert_id == "yaml_expert"
    assert expert.version == "1.0"
    assert "read_file" in expert.allowed_tools
    assert "shell_exec" in expert.denied_tools
    assert expert.output_contract == "dispatch_plan"


# ── Registry behaviour ────────────────────────────────────────────────────────

def test_registry_get_unknown():
    registry = ExpertRegistry.__new__(ExpertRegistry)
    registry._experts = {}
    assert registry.get("nonexistent_expert") is None


def test_registry_validate_all_valid(tmp_path: Path):
    yaml_content = textwrap.dedent("""\
        expert_id: valid_expert
        version: "1.0"
        title: Valid Expert
        purpose: "Immer valide"
        allowed_tools: []
        denied_tools: []
        allowed_path_patterns: []
        denied_path_patterns: []
        model_routing: {}
        context_strategy: minimal
        output_contract: dispatch_plan
        approval_gates: []
        min_policy_scope: project
    """)
    (tmp_path / "valid_expert.yaml").write_text(yaml_content)

    registry = ExpertRegistry(config_dir=tmp_path)
    registry.load_all()
    result = registry.validate_all()
    # No errors expected for a fully valid definition
    assert result == {}


def test_registry_duplicate_expert_id_raises(tmp_path: Path):
    base_yaml = textwrap.dedent("""\
        expert_id: dup_expert
        version: "1.0"
        title: Dup Expert
        purpose: "Doppelt"
        allowed_tools: []
        denied_tools: []
        allowed_path_patterns: []
        denied_path_patterns: []
        model_routing: {}
        context_strategy: minimal
        output_contract: dispatch_plan
        approval_gates: []
        min_policy_scope: project
    """)
    (tmp_path / "dup_expert_a.yaml").write_text(base_yaml)
    (tmp_path / "dup_expert_b.yaml").write_text(base_yaml)

    registry = ExpertRegistry(config_dir=tmp_path)
    with pytest.raises(ValueError, match="Duplicate expert_id"):
        registry.load_all()
