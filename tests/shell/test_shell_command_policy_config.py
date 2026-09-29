"""SCG-017: Config override tests for shell_command_policy."""
from __future__ import annotations

from agent.services.shell_command_policy import ShellCommandAnalyzer, ShellCommandPolicy


def _analyze(cmd: str, **policy_kwargs) -> bool:
    policy = ShellCommandPolicy(**policy_kwargs)
    return ShellCommandAnalyzer().analyze_with_policy(cmd, policy).allowed


# ── Semicolon control ─────────────────────────────────────────────────────────


def test_semicolon_blocked_when_not_in_allow_list():
    assert _analyze("pytest; git status", allow_chain_operators=["&&", "||"]) is False


def test_semicolon_allowed_by_default():
    assert _analyze("pytest; git status") is True


def test_and_and_allowed():
    assert _analyze("pytest && git status") is True


def test_or_or_allowed():
    assert _analyze("pytest || echo failed") is True


# ── Per-operator restrictions ─────────────────────────────────────────────────


def test_and_and_blocked_when_only_semicolon_allowed():
    assert _analyze("pytest && git status", allow_chain_operators=[";"]) is False


def test_or_or_blocked_when_only_and_and_allowed():
    assert _analyze("pytest || echo failed", allow_chain_operators=["&&"]) is False


def test_allow_and_and_blocks_semicolon_and_or_or():
    assert _analyze("pytest && git status", allow_chain_operators=["&&"]) is True
    assert _analyze("pytest; git status", allow_chain_operators=["&&"]) is False
    assert _analyze("pytest || echo failed", allow_chain_operators=["&&"]) is False


# ── Empty allow-list blocks all chains ───────────────────────────────────────


def test_empty_allow_list_blocks_all_chain_operators():
    for cmd in ["pytest; git status", "pytest && git status", "pytest || echo failed"]:
        assert _analyze(cmd, allow_chain_operators=[]) is False, f"should be blocked: {cmd}"


def test_empty_allow_list_allows_single_segment():
    assert _analyze("pytest --tb=short", allow_chain_operators=[]) is True


# ── Quoted operator handling ──────────────────────────────────────────────────


def test_quoted_semicolon_allowed_even_when_shell_semicolon_blocked():
    """python -c 'a=1; b=2' contains a Python-level semicolon that must not be flagged."""
    result = ShellCommandAnalyzer().analyze_with_policy(
        "python -c 'a=1; b=2'",
        ShellCommandPolicy(allow_chain_operators=["&&", "||"]),
    )
    assert result.allowed is True
    assert result.contains_chain is False


def test_quoted_semicolon_in_double_quotes_allowed_when_shell_semicolon_blocked():
    result = ShellCommandAnalyzer().analyze_with_policy(
        'python -c "a=1; b=2"',
        ShellCommandPolicy(allow_chain_operators=["&&", "||"]),
    )
    assert result.allowed is True
    assert result.contains_chain is False


# ── Pipe always denied (even if not in deny_operators by override) ────────────


def test_pipe_remains_blocked_as_unsupported_by_parser():
    """CommandChainParser marks | as unsupported; ShellCommandPolicy deny_operators is an extra check."""
    result = ShellCommandAnalyzer().analyze("cat file | grep x")
    assert result.allowed is False


# ── Policy via agent_cfg ──────────────────────────────────────────────────────


def test_policy_loaded_from_agent_cfg():
    agent_cfg = {"shell_command_policy": {"allow_chain_operators": ["&&"]}}
    result = ShellCommandAnalyzer().analyze("pytest; git status", agent_cfg=agent_cfg)
    assert result.allowed is False
    result2 = ShellCommandAnalyzer().analyze("pytest && git status", agent_cfg=agent_cfg)
    assert result2.allowed is True
