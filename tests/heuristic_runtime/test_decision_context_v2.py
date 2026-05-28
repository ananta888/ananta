"""Tests für DecisionContext v2 — TUI-Snapshot-Referenzen und Hash-Stabilität."""
import pytest

from agent.services.heuristic_runtime.decision_context import DecisionContext, build_from_tui_state


def test_hash_stable_with_same_snapshot():
    """Gleicher tui_snapshot_ref → gleicher context_hash."""
    ctx1 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        semantic_hash="def456",
    )
    ctx2 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        semantic_hash="def456",
    )
    assert ctx1.context_hash == ctx2.context_hash


def test_hash_changes_on_snapshot_change():
    """Anderer tui_snapshot_ref → anderer context_hash."""
    ctx1 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
    )
    ctx2 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="xyz999",
    )
    assert ctx1.context_hash != ctx2.context_hash


def test_hash_changes_on_semantic_hash_change():
    """Anderer semantic_hash → anderer context_hash."""
    ctx1 = DecisionContext(
        source_surface="tui_snake",
        semantic_hash="hash_a",
    )
    ctx2 = DecisionContext(
        source_surface="tui_snake",
        semantic_hash="hash_b",
    )
    assert ctx1.context_hash != ctx2.context_hash


def test_tui_delta_ref_not_in_context_hash():
    """tui_delta_ref ist volatile und wird nicht in context_hash einbezogen."""
    ctx1 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        tui_delta_ref="prev:curr",
    )
    ctx2 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        tui_delta_ref="other:delta",
    )
    # tui_delta_ref soll den hash nicht verändern
    assert ctx1.context_hash == ctx2.context_hash


def test_semantic_panel_not_in_context_hash():
    """semantic_panel ist ein Display-Detail und soll den context_hash nicht ändern."""
    ctx1 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        semantic_panel="BODY",
    )
    ctx2 = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="abc123",
        semantic_panel="CHAT",
    )
    # semantic_panel soll den hash nicht verändern
    assert ctx1.context_hash == ctx2.context_hash


def test_to_dict_includes_v2_fields():
    """to_dict() enthält alle v2-Felder."""
    ctx = DecisionContext(
        source_surface="tui_snake",
        tui_snapshot_ref="snap1",
        tui_delta_ref="prev:curr",
        semantic_hash="sem_hash",
        semantic_panel="BODY",
    )
    d = ctx.to_dict()
    assert d["tui_snapshot_ref"] == "snap1"
    assert d["tui_delta_ref"] == "prev:curr"
    assert d["semantic_hash"] == "sem_hash"
    assert d["semantic_panel"] == "BODY"


def test_to_dict_v1_compat_none_fields():
    """to_dict() bei v1-Daten: neue Felder sind None."""
    ctx = DecisionContext(source_surface="tui_snake")
    d = ctx.to_dict()
    assert d["tui_snapshot_ref"] is None
    assert d["tui_delta_ref"] is None
    assert d["semantic_hash"] is None
    assert d["semantic_panel"] is None


def test_backward_compat_v1_data():
    """DecisionContext funktioniert ohne v2-Felder (Rückwärtskompatibilität)."""
    ctx = DecisionContext(
        source_surface="tui_snake",
        ai_status="available",
        active_goal_id="goal-1",
    )
    assert ctx.tui_snapshot_ref is None
    assert ctx.tui_delta_ref is None
    assert ctx.semantic_hash is None
    assert ctx.semantic_panel is None
    # context_hash sollte trotzdem berechenbar sein
    assert len(ctx.context_hash) == 16


def test_build_from_tui_state_with_new_params():
    """build_from_tui_state() akzeptiert neue v2-Parameter."""
    ctx = build_from_tui_state(
        tui_state={"active_panel": "BODY"},
        snapshot_ref="snap_hash_123",
        delta_ref="prev_hash:curr_hash",
        semantic_hash="sem_abc",
        semantic_panel="BODY",
    )
    assert ctx.tui_snapshot_ref == "snap_hash_123"
    assert ctx.tui_delta_ref == "prev_hash:curr_hash"
    assert ctx.semantic_hash == "sem_abc"
    assert ctx.semantic_panel == "BODY"
    assert ctx.source_surface == "tui_snake"


def test_build_from_tui_state_backward_compat():
    """build_from_tui_state() ohne neue Parameter bleibt rückwärtskompatibel."""
    ctx = build_from_tui_state(tui_state={"active_panel": "BODY"})
    assert ctx.tui_snapshot_ref is None
    assert ctx.tui_delta_ref is None
    assert ctx.semantic_hash is None
    assert ctx.semantic_panel is None


def test_hash_stable_across_instances():
    """Mehrfach erstellte identische Contexts haben gleiche Hashes."""
    kwargs = dict(
        source_surface="tui_snake",
        ai_status="available",
        active_goal_id="g1",
        tui_snapshot_ref="snap42",
        semantic_hash="shash",
    )
    hashes = [DecisionContext(**kwargs).context_hash for _ in range(5)]
    assert len(set(hashes)) == 1, "Hash muss deterministisch sein"


def test_hash_changes_on_snake_head_change():
    ctx1 = DecisionContext(source_surface="tui_snake", snake_head_x=10, snake_head_y=5)
    ctx2 = DecisionContext(source_surface="tui_snake", snake_head_x=11, snake_head_y=5)
    assert ctx1.context_hash != ctx2.context_hash


def test_build_from_tui_state_extracts_snake_head_from_header_logo_game():
    ctx = build_from_tui_state(
        tui_state={
            "header_logo_game": {
                "local_snake_id": "s1",
                "snakes": {"s1": {"snake": [[42, 17], [41, 17]]}},
            }
        }
    )
    assert ctx.snake_head_x == 42
    assert ctx.snake_head_y == 17
