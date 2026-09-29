from __future__ import annotations

from unittest.mock import patch

from agent.services.prompt_trace_access_policy import PromptTraceAccessPolicy


def test_raw_access_denied_when_raw_not_available():
    policy = PromptTraceAccessPolicy()
    decision = policy.check_raw_access(is_admin=True, is_local=True, raw_available=False)
    assert decision.allowed is False
    assert decision.reason == "raw_not_stored"


def test_raw_access_denied_when_store_raw_disabled():
    policy = PromptTraceAccessPolicy()
    with patch("agent.config.settings.prompt_trace_store_raw_prompts", False), patch(
        "agent.config.settings.prompt_trace_allowed_raw_access_modes", ["admin", "local_admin_debug"]
    ):
        decision = policy.check_raw_access(is_admin=True, is_local=True, raw_available=True)
    assert decision.allowed is False
    assert decision.reason == "store_raw_prompts_disabled"


def test_raw_access_allowed_for_admin_mode():
    policy = PromptTraceAccessPolicy()
    with patch("agent.config.settings.prompt_trace_store_raw_prompts", True), patch(
        "agent.config.settings.prompt_trace_allowed_raw_access_modes", ["admin"]
    ):
        decision = policy.check_raw_access(is_admin=True, is_local=False, raw_available=True)
    assert decision.allowed is True
    assert decision.reason == "admin_allowed"
