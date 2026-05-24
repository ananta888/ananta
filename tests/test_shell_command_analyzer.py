"""SCG-003 / SCG-002: ShellCommandAnalyzer and ShellCommandPolicy tests."""
from __future__ import annotations

from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy

_OPENCODE_CMD = 'python hello.py; python -c "from hello import greet; print(greet(\'World\'))"'


# ── SCG-002: CommandChainAnalysisResult structure ─────────────────────────────


def test_analyzer_allows_default_semicolon_chain():
    result = ShellCommandAnalyzer().analyze(_OPENCODE_CMD)
    assert result.allowed is True
    assert result.contains_chain is True
    assert len(result.segments) == 2
    assert result.segments[0].raw == "python hello.py"
    assert result.chain_operator_count == 1
    assert result.denied_reason is None
    assert result.unsupported_operators == []


def test_analyzer_opencode_second_segment_has_quoted_semicolon():
    """The second segment must contain the Python-level semicolon (quoted, not a shell split)."""
    result = ShellCommandAnalyzer().analyze(_OPENCODE_CMD)
    assert len(result.segments) == 2
    second = result.segments[1].raw
    assert second.startswith('python -c')
    assert ";" in second
    assert result.contains_quoted_operators is True


def test_analyzer_blocks_pipe_by_default():
    result = ShellCommandAnalyzer().analyze("cat a | grep x")
    assert result.allowed is False
    assert result.denied_reason == "unsupported_operator"
    assert "|" in result.unsupported_operators


def test_analyzer_blocks_command_substitution():
    for cmd in ["echo $(whoami)", "echo ${HOME}"]:
        result = ShellCommandAnalyzer().analyze(cmd)
        assert result.allowed is False, f"Expected blocked for: {cmd}"


def test_analyzer_blocks_backtick():
    result = ShellCommandAnalyzer().analyze("echo `whoami`")
    assert result.allowed is False
    assert "`" in result.unsupported_operators


def test_analyzer_blocks_heredoc():
    result = ShellCommandAnalyzer().analyze("cat << EOF\nhello\nEOF")
    assert result.allowed is False
    assert "<<" in result.unsupported_operators


def test_analyzer_blocks_trailing_operator():
    result = ShellCommandAnalyzer().analyze("pytest &&")
    assert result.allowed is False
    assert result.denied_reason == "trailing_operator"


def test_analyzer_empty_segment_is_blocked():
    result = ShellCommandAnalyzer().analyze("; pytest")
    assert result.allowed is False
    assert result.denied_reason == "empty_segment"


def test_analyzer_single_segment_command_allowed():
    result = ShellCommandAnalyzer().analyze("pytest --tb=short")
    assert result.allowed is True
    assert result.contains_chain is False
    assert len(result.segments) == 1


def test_analyzer_empty_command_allowed():
    result = ShellCommandAnalyzer().analyze("")
    assert result.allowed is True
    assert result.contains_chain is False
    assert result.segments == []


# ── SCG-002: as_dict structure ────────────────────────────────────────────────


def test_analyzer_result_as_dict_structure():
    result = ShellCommandAnalyzer().analyze("pytest && git status")
    d = result.as_dict()
    assert d["allowed"] is True
    assert d["segment_count"] == 2
    assert d["contains_chain"] is True
    assert d["chain_operator_count"] == 1
    assert "original_command" in d
    assert "unsupported_operators" in d


# ── SCG-001: ShellCommandPolicy ──────────────────────────────────────────────


def test_policy_defaults():
    p = ShellCommandPolicy()
    assert ";" in p.allow_chain_operators
    assert "&&" in p.allow_chain_operators
    assert "||" in p.allow_chain_operators
    assert "|" in p.deny_operators
    assert "`" in p.deny_operators
    assert p.validate_segments_individually is True
    assert p.allow_quoted_operators is True


def test_policy_from_config_empty():
    p = ShellCommandPolicy.from_config(None)
    assert p.enabled is True
    assert ";" in p.allow_chain_operators


def test_policy_from_config_override_semicolon():
    p = ShellCommandPolicy.from_config({"allow_chain_operators": ["&&", "||"]})
    assert ";" not in p.allow_chain_operators
    assert "&&" in p.allow_chain_operators


def test_policy_from_agent_cfg():
    agent_cfg = {"shell_command_policy": {"allow_chain_operators": ["&&"]}}
    p = ShellCommandPolicy.from_agent_cfg(agent_cfg)
    assert p.allow_chain_operators == ["&&"]


# ── SCG-003: policy-override behavior ────────────────────────────────────────


def test_analyzer_respects_policy_denied_semicolon():
    """When ';' is removed from allow_chain_operators, shell-semicolons are blocked."""
    policy = ShellCommandPolicy(allow_chain_operators=["&&", "||"])
    result = ShellCommandAnalyzer().analyze_with_policy("pytest; git status", policy)
    assert result.allowed is False
    assert result.denied_reason == "chain_operator_not_allowed"
    assert ";" in result.unsupported_operators


def test_analyzer_allows_quoted_semicolon_even_when_shell_semicolon_blocked():
    """Quoted semicolons are never shell operators regardless of policy."""
    policy = ShellCommandPolicy(allow_chain_operators=["&&", "||"])
    result = ShellCommandAnalyzer().analyze_with_policy('python -c "a=1; b=2"', policy)
    assert result.allowed is True
    assert result.contains_chain is False


def test_analyzer_policy_allow_only_and():
    policy = ShellCommandPolicy(allow_chain_operators=["&&"])
    assert ShellCommandAnalyzer().analyze_with_policy("pytest && git status", policy).allowed is True
    assert ShellCommandAnalyzer().analyze_with_policy("pytest || echo failed", policy).allowed is False
    assert ShellCommandAnalyzer().analyze_with_policy("pytest; git status", policy).allowed is False


def test_analyzer_policy_deny_all_chain_operators():
    policy = ShellCommandPolicy(allow_chain_operators=[])
    assert ShellCommandAnalyzer().analyze_with_policy("pytest; git status", policy).allowed is False
    assert ShellCommandAnalyzer().analyze_with_policy("pytest && git status", policy).allowed is False
    assert ShellCommandAnalyzer().analyze_with_policy("pytest", policy).allowed is True
