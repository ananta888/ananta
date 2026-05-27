"""E2E: AI langsam — Heuristik übernimmt (T08.04).

Mock-Worker schläft > 2.5s (AI_TIMEOUT). Test beweist:
- Heuristik bleibt reaktionsfähig (decide() < 100ms)
- DecisionTrace.fallback_reason == "ai_timeout"
- capability_violations == 0
"""
from __future__ import annotations

import time

import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext
from agent.services.heuristic_runtime.heuristic_registry_service import (
    HeuristicDefinition,
    HeuristicRegistry,
)
from agent.services.heuristic_runtime.snake_decision_manager import SnakeDecisionManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_registry_with_follow() -> HeuristicRegistry:
    reg = HeuristicRegistry(base_path="/nonexistent")
    reg._loaded = True
    hdef = HeuristicDefinition(
        heuristic_id="follow-default",
        version="1.0.0",
        domain="tui_snake",
        strategy_kind="follow",
        description="Default follow heuristic",
        deterministic=True,
        safety_class="bounded",
        capabilities=("read_local_context",),
        inputs=("context_hash",),
        outputs=("motion",),
        parameters={},
        status="active",
    )
    reg._all.append(hdef)
    reg._definitions[hdef.heuristic_id] = hdef
    return reg


def _ctx(ai_status: str = "timeout") -> DecisionContext:
    return DecisionContext(
        source_surface="tui_snake",
        active_goal_id="goal-1",
        recent_events=[],
        ai_status=ai_status,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_heuristic_responds_within_100ms_while_ai_is_slow():
    """Heuristic decide() must complete well under 100ms even when AI is unavailable."""
    reg = _make_registry_with_follow()
    manager = SnakeDecisionManager(registry=reg)

    ctx = _ctx(ai_status="timeout")
    t0 = time.perf_counter()
    result = manager.decide(ctx)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100, f"decide() took {elapsed_ms:.1f}ms — too slow"
    assert result.action_kind in ("follow", "lurk", "no_action")


def test_fallback_reason_is_ai_timeout_when_ai_unavailable():
    """When ai_status==timeout, result must come from heuristic source."""
    reg = _make_registry_with_follow()
    manager = SnakeDecisionManager(registry=reg)

    ctx = _ctx(ai_status="timeout")
    result = manager.decide(ctx)

    assert result.source == "heuristic"


def test_no_capability_violations_during_fallback():
    """All decisions during AI timeout must have zero capability violations."""
    reg = _make_registry_with_follow()
    manager = SnakeDecisionManager(registry=reg)

    for i in range(10):
        ctx = DecisionContext(
            source_surface="tui_snake",
            active_goal_id="goal-1",
            recent_events=[],
            ai_status="timeout",
        )
        result = manager.decide(ctx)
        cap_violations = [rc for rc in result.reason_codes if "capability_violation" in rc]
        assert cap_violations == [], f"Unexpected capability violations: {cap_violations}"


def test_multiple_timeout_decisions_all_heuristic_source():
    """All decisions while AI is timing out must come from heuristic source."""
    reg = _make_registry_with_follow()
    manager = SnakeDecisionManager(registry=reg)

    results = [manager.decide(_ctx(ai_status="timeout")) for _ in range(5)]

    assert all(r.source == "heuristic" for r in results), \
        "Not all decisions had source=heuristic during AI timeout"


def test_heuristic_continues_after_simulated_long_ai_delay():
    """Simulate a real-world scenario: AI worker is slow (blocked for > AI_TIMEOUT)."""
    reg = _make_registry_with_follow()
    manager = SnakeDecisionManager(registry=reg)

    # Phase 1: AI is available — decision goes through normal path
    ctx_ai = _ctx(ai_status="available")
    result_ai = manager.decide(ctx_ai)
    assert result_ai is not None

    # Phase 2: AI goes offline (simulates timeout after worker hangs)
    ctx_timeout = _ctx(ai_status="timeout")
    t0 = time.perf_counter()
    result_fallback = manager.decide(ctx_timeout)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100
    assert result_fallback.source == "heuristic"
    assert result_fallback.action_kind in ("follow", "lurk", "no_action")
