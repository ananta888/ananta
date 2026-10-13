"""SEH-008: online context compaction preserves state invariants."""

from __future__ import annotations

from agent.services.harness_compaction import ContextCompactor, ContextItem


def item(item_id, kind, text, created_at=0.0):
    return ContextItem(item_id=item_id, kind=kind, text=text, created_at=created_at)


def test_open_obligations_constraints_errors_and_evidence_survive():
    items = [
        item("plan", "plan", "step 1: build, step 2: test", 1),
        item("obl", "open_obligation", "must ship before friday", 2),
        item("con", "constraint", "never touch production", 3),
        item("err", "error_state", "last build failed", 4),
        item("h1", "evidence_handle", "obs://deadbeef", 5),
        item("work", "completed_work", "z" * 4000, 6),
        item("obs", "observation", "y" * 4000, 7),
    ]
    result = ContextCompactor().compact(items, budget_tokens=200)
    kept = {i.item_id for i in result.kept}
    assert {"plan", "obl", "con", "err", "h1"} <= kept
    assert {"work", "obs"}.isdisjoint(kept)
    assert set(result.dropped) == {"work", "obs"}
    assert ContextCompactor.invariant_holds(items, result)


def test_invariant_holds_across_many_compactions():
    items = [
        item("obl", "open_obligation", "obligation", 1),
        item("con", "constraint", "constraint", 2),
        item("err", "error_state", "error", 3),
        item("h1", "evidence_handle", "obs://1", 4),
        item("plan", "plan", "plan", 5),
    ]
    compactor = ContextCompactor()
    current = list(items)
    for round_index in range(50):
        current = list(current) + [item(f"work-{round_index}", "completed_work", "w" * 400, 100 + round_index)]
        result = compactor.compact(current, budget_tokens=120)
        assert ContextCompactor.invariant_holds(current, result)
        current = list(result.kept)
    kept = {i.item_id for i in current}
    assert {"obl", "con", "err", "h1", "plan"} <= kept


def test_preserved_exceeding_budget_fails_closed_keeping_everything():
    items = [item("obl", "open_obligation", "x" * 8000, 1)]
    result = ContextCompactor().compact(items, budget_tokens=10)
    assert result.fallback is True and result.reason == "compaction_preserved_exceeds_budget"
    assert {i.item_id for i in result.kept} == {"obl"}


def test_dropped_items_are_traceable():
    items = [item("work", "completed_work", "w" * 800, 1), item("msg", "message", "recent", 2)]
    result = ContextCompactor().compact(items, budget_tokens=50)
    assert "work" in result.dropped
    assert result.trace["redundant"] == 1


def test_invalid_budget_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="compaction_budget_invalid"):
        ContextCompactor().compact([], budget_tokens=0)
