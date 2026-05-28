"""Tests für DecisionTrace mit Snapshot-Referenzen (v2)."""
import pytest

from agent.services.heuristic_runtime.decision_trace import DecisionTrace


def test_snapshot_fields_optional_none_by_default():
    """Neue Snapshot-Felder sind Optional und standardmäßig None."""
    trace = DecisionTrace(surface="tui_snake", context_hash="abc")
    assert trace.snapshot_hash is None
    assert trace.delta_hash is None
    assert trace.semantic_hash is None
    assert trace.heuristic_experiment_id is None


def test_snapshot_fields_can_be_set():
    """Snapshot-Felder können gesetzt werden."""
    trace = DecisionTrace(
        surface="tui_snake",
        context_hash="abc",
        snapshot_hash="snap_hash_1",
        delta_hash="delta_hash_1",
        semantic_hash="sem_hash_1",
        heuristic_experiment_id="exp_42",
    )
    assert trace.snapshot_hash == "snap_hash_1"
    assert trace.delta_hash == "delta_hash_1"
    assert trace.semantic_hash == "sem_hash_1"
    assert trace.heuristic_experiment_id == "exp_42"


def test_existing_fields_unchanged():
    """Bestehende Felder sind durch v2-Erweiterung unverändert."""
    trace = DecisionTrace(
        surface="tui_snake",
        context_hash="ctx_hash",
        lease_id="lease_1",
        heuristic_id="h_id",
        strategy_id="s_id",
        rule_id="r_id",
        confidence=0.9,
        source="heuristic",
        action_kind="follow",
        reason_codes=["test_reason"],
    )
    assert trace.surface == "tui_snake"
    assert trace.context_hash == "ctx_hash"
    assert trace.lease_id == "lease_1"
    assert trace.heuristic_id == "h_id"
    assert trace.strategy_id == "s_id"
    assert trace.rule_id == "r_id"
    assert trace.confidence == 0.9
    assert trace.source == "heuristic"
    assert trace.action_kind == "follow"
    assert trace.reason_codes == ["test_reason"]


def test_to_dict_includes_snapshot_fields():
    """to_dict() enthält neue Snapshot-Felder."""
    trace = DecisionTrace(
        surface="tui_snake",
        context_hash="ctx",
        snapshot_hash="snap_1",
        delta_hash="delta_1",
        semantic_hash="sem_1",
        heuristic_experiment_id="exp_1",
    )
    d = trace.to_dict()
    assert d["snapshot_hash"] == "snap_1"
    assert d["delta_hash"] == "delta_1"
    assert d["semantic_hash"] == "sem_1"
    assert d["heuristic_experiment_id"] == "exp_1"


def test_to_dict_none_snapshot_fields():
    """to_dict() gibt None für fehlende Snapshot-Felder zurück."""
    trace = DecisionTrace(surface="tui_snake", context_hash="ctx")
    d = trace.to_dict()
    assert d["snapshot_hash"] is None
    assert d["delta_hash"] is None
    assert d["semantic_hash"] is None
    assert d["heuristic_experiment_id"] is None


def test_to_dict_contains_legacy_fields():
    """to_dict() enthält alle bestehenden Felder."""
    trace = DecisionTrace(surface="tui_snake", context_hash="ctx", confidence=0.5)
    d = trace.to_dict()
    assert "event_id" in d
    assert "surface" in d
    assert "context_hash" in d
    assert "confidence" in d
    assert "action_kind" in d
    assert "reason_codes" in d


def test_missing_snapshot_fields_do_not_block_resolve():
    """Fehlende Snapshot-Felder blockieren nicht resolve()."""
    trace = DecisionTrace(surface="tui_snake", context_hash="ctx")
    trace.resolve()
    assert trace.resolved_at is not None
    assert trace.duration_ms is not None
    assert trace.duration_ms >= 0.0


def test_from_decision_result_backward_compat():
    """from_decision_result() funktioniert ohne Snapshot-Refs."""
    from agent.services.heuristic_runtime.decision_result import DecisionResult
    result = DecisionResult.heuristic_follow(dx=1, dy=0, strategy_id="test_strategy")
    trace = DecisionTrace.from_decision_result(
        result, surface="tui_snake", context_hash="ctx_hash"
    )
    assert trace.surface == "tui_snake"
    assert trace.context_hash == "ctx_hash"
    assert trace.snapshot_hash is None
    assert trace.delta_hash is None
