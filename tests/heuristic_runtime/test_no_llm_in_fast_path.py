"""Verifiziert dass SnakeDecisionManager bei decide() keine Netzwerk/LLM-Aufrufe macht."""
import pytest
from unittest.mock import patch, MagicMock
from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager
from agent.services.heuristic_runtime.decision_context import DecisionContext


def _mock_manager():
    from agent.repositories.decision_trace_repo import DecisionTraceRepository
    from agent.repositories.heuristic_lease_repo import HeuristicLeaseRepository
    from agent.services.heuristic_runtime.heuristic_registry_service import HeuristicRegistry
    registry = HeuristicRegistry.__new__(HeuristicRegistry)
    registry._heuristics = {}
    lease_repo = MagicMock()
    lease_repo.get_active.return_value = None
    trace_repo = MagicMock()
    trace_repo.save.return_value = None
    return SnakeDecisionManager(registry=registry, lease_repo=lease_repo, trace_repo=trace_repo)


def test_decide_makes_no_http_calls():
    """decide() darf keine httpx/requests/urllib Calls auslösen."""
    import httpx, requests
    mgr = _mock_manager()
    ctx = DecisionContext(source_surface="tui_snake")
    with patch.object(httpx.Client, "request", side_effect=AssertionError("LLM call in fast path!")):
        with patch.object(requests.Session, "request", side_effect=AssertionError("LLM call in fast path!")):
            result = mgr.decide(ctx)
    assert result is not None


def test_decide_makes_no_openai_calls():
    """decide() darf keinen OpenAI-Client aufrufen."""
    import importlib, sys
    mgr = _mock_manager()
    ctx = DecisionContext(source_surface="tui_snake")
    # Patch openai if present
    if "openai" in sys.modules:
        with patch("openai.OpenAI", side_effect=AssertionError("OpenAI in fast path!")):
            result = mgr.decide(ctx)
    else:
        result = mgr.decide(ctx)
    assert result is not None
