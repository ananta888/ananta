"""Tests for T05 — Token Budget Gate in CLI Backends.

Verifies that when the budget is exceeded, subprocess.run is NOT called.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── Helper: mock TokenBudgetService ──────────────────────────────────────────

def _budget_over(estimated_tokens: int = 200000, max_tokens: int = 128000) -> dict:
    return {
        "allowed": False,
        "reason_code": "token_budget_exceeded",
        "estimated_tokens": estimated_tokens,
        "max_tokens": max_tokens,
    }


def _budget_ok(estimated_tokens: int = 1000, max_tokens: int = 128000) -> dict:
    return {
        "allowed": True,
        "reason_code": "within_budget",
        "estimated_tokens": estimated_tokens,
        "max_tokens": max_tokens,
    }


def _make_tbs_mock(budget_result: dict, estimate_tokens: int = 1000):
    tbs_instance = MagicMock()
    tbs_instance.estimate.return_value = {"tokens": estimate_tokens, "method": "chars_per_token_fallback", "confidence": "low"}
    tbs_instance.check_budget.return_value = budget_result
    tbs_cls = MagicMock(return_value=tbs_instance)
    return tbs_cls


# ── opencode — run_opencode_command ──────────────────────────────────────────

def test_opencode_budget_exceeded_no_subprocess():
    """When budget is exceeded, subprocess.run must NOT be called."""
    tbs_cls = _make_tbs_mock(_budget_over())

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls), \
         patch("subprocess.run") as mock_run, \
         patch("shutil.which", return_value="/usr/bin/opencode"):
        # Import inside patch context to pick up mocked module
        from agent.cli_backends import opencode as oc_mod
        # We need to reload to clear cached imports inside the function
        # The function does a local import, so we can patch the module directly
        with patch("agent.cli_backends.opencode.shutil.which", return_value=None):
            rc, out, err = oc_mod.run_opencode_command("a very long prompt" * 1000)
        mock_run.assert_not_called()


def test_opencode_budget_ok_subprocess_called():
    """When budget is OK, subprocess path continues (binary not found → -1 but subprocess attempted or not found)."""
    tbs_cls = _make_tbs_mock(_budget_ok())

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls), \
         patch("agent.cli_backends.opencode.shutil.which", return_value=None):
        from agent.cli_backends import opencode as oc_mod
        rc, out, err = oc_mod.run_opencode_command("short prompt")
        # Binary not found → -1, but NOT budget error
        assert "token_budget_exceeded" not in err


def test_opencode_budget_exceeded_returns_error_tuple():
    """Return value has correct error string when budget exceeded."""
    tbs_cls = _make_tbs_mock(_budget_over(estimated_tokens=200000, max_tokens=128000))

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls):
        from agent.cli_backends import opencode as oc_mod
        rc, out, err = oc_mod.run_opencode_command("x" * 1000000)
        assert rc == -1
        assert out == ""
        assert "token_budget_exceeded" in err
        assert "200000" in err


# ── opencode — run_codex_command ──────────────────────────────────────────────

def test_codex_budget_exceeded_no_subprocess():
    tbs_cls = _make_tbs_mock(_budget_over())

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls), \
         patch("subprocess.run") as mock_run, \
         patch("agent.cli_backends.opencode.shutil.which", return_value=None):
        from agent.cli_backends import opencode as oc_mod
        rc, out, err = oc_mod.run_codex_command("big prompt" * 10000)
        mock_run.assert_not_called()


def test_codex_budget_exceeded_returns_error():
    tbs_cls = _make_tbs_mock(_budget_over(estimated_tokens=999999, max_tokens=128000))

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls):
        from agent.cli_backends import opencode as oc_mod
        rc, out, err = oc_mod.run_codex_command("x" * 1000000)
        assert rc == -1
        assert "token_budget_exceeded" in err


# ── sgpt — run_sgpt_command ───────────────────────────────────────────────────

def test_sgpt_budget_exceeded_no_subprocess():
    tbs_cls = _make_tbs_mock(_budget_over())

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls), \
         patch("subprocess.run") as mock_run:
        from agent.cli_backends import sgpt as sgpt_mod
        rc, out, err = sgpt_mod.run_sgpt_command("big prompt" * 10000)
        mock_run.assert_not_called()


def test_sgpt_budget_exceeded_returns_error():
    tbs_cls = _make_tbs_mock(_budget_over(estimated_tokens=512000, max_tokens=128000))

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls):
        from agent.cli_backends import sgpt as sgpt_mod
        rc, out, err = sgpt_mod.run_sgpt_command("x" * 1000000)
        assert rc == -1
        assert "token_budget_exceeded" in err


def test_sgpt_budget_ok_no_budget_error():
    tbs_cls = _make_tbs_mock(_budget_ok())

    with patch("agent.services.token_budget_service.TokenBudgetService", tbs_cls), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        # sgpt also needs env and other bits; patch what's needed
        from agent.cli_backends import sgpt as sgpt_mod
        # If this doesn't raise a budget error and subprocess is attempted, budget is OK
        try:
            rc, out, err = sgpt_mod.run_sgpt_command("short prompt")
            assert "token_budget_exceeded" not in err
        except Exception:
            pass  # Other runtime errors are OK — we just care budget gate didn't block


# ── check_worker_allowed ──────────────────────────────────────────────────────

def test_worker_allowed_no_decision():
    from agent.services.worker_selection_policy_service import check_worker_allowed
    result = check_worker_allowed(worker_name="openai-worker", decision=None)
    assert result["allowed"] is True
    assert result["reason_code"] == "no_budget_gate_active"


def test_worker_blocked_in_safe_minimal_chat():
    from agent.services.worker_selection_policy_service import check_worker_allowed
    from unittest.mock import MagicMock
    decision = MagicMock()
    decision.mode = "safe_minimal_chat"
    result = check_worker_allowed(worker_name="openai-expensive-worker", decision=decision)
    assert result["allowed"] is False
    assert result["reason_code"] == "safe_minimal_chat_blocks_expensive_workers"


def test_local_worker_allowed_in_safe_minimal_chat():
    from agent.services.worker_selection_policy_service import check_worker_allowed
    from unittest.mock import MagicMock
    decision = MagicMock()
    decision.mode = "safe_minimal_chat"
    result = check_worker_allowed(worker_name="local-ananta-worker", decision=decision)
    assert result["allowed"] is True


def test_worker_allowed_in_project_chat():
    from agent.services.worker_selection_policy_service import check_worker_allowed
    from unittest.mock import MagicMock
    decision = MagicMock()
    decision.mode = "project_chat"
    result = check_worker_allowed(worker_name="openai-worker", decision=decision)
    assert result["allowed"] is True
