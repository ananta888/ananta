"""WCRB-012: retired CodeCompass search tools are answered by their successor for workers."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.timeout(30)
WORKER_TOOLS = ["repo.grep", "codecompass.search", "codecompass.search_symbols", "codecompass.get_file_context"]


def _resolve(name, allowed, arguments=None):
    from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service

    return get_ananta_tool_registry_service().resolve_alias(name, allowed, arguments)


def test_retired_search_tools_become_codecompass_search_with_compatible_arguments():
    tool, arguments, aliased = _resolve("codecompass.retrieve", WORKER_TOOLS,
                                        {"query": "CircuitBreaker", "mode": "hybrid", "budget": {"x": 1}})
    assert (tool, aliased) == ("codecompass.search", "codecompass.retrieve")
    assert arguments["query"] == "CircuitBreaker" and "budget" not in arguments  # search has no budget
    assert _resolve("codecompass.resolve_context", WORKER_TOOLS, {"query": "q"})[0] == "codecompass.search"


def test_aliases_do_not_touch_allowed_unknown_or_unmapped_tools():
    assert _resolve("codecompass.retrieve", WORKER_TOOLS + ["codecompass.retrieve"])[2] is None  # still offered
    assert _resolve("codecompass.retrieve", ["repo.grep"])[0] == "codecompass.retrieve"  # successor not allowed
    assert _resolve("repo.grep", WORKER_TOOLS) == ("repo.grep", {}, None)


def test_the_default_worker_allowlist_offers_the_reduced_set():
    from agent.config_defaults import build_default_agent_config

    allowed = build_default_agent_config()["ananta_worker_tool_loop"]["allowed_tools"]
    assert "codecompass.search" in allowed and "codecompass.search_symbols" in allowed
    assert "codecompass.retrieve" not in allowed and "codecompass.resolve_context" not in allowed
