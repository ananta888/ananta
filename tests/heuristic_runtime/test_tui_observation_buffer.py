"""Tests für TuiObservationBuffer — Ringbuffer-Verhalten."""
import time
import pytest

from agent.services.heuristic_runtime.tui_observation_buffer import (
    TuiObservationBuffer,
    SnapshotRef,
    DeltaRef,
)


def _make_snapshot(frame_id: str, screen_hash: str, ts: float = 0.0) -> SnapshotRef:
    return SnapshotRef(
        frame_id=frame_id,
        screen_hash=screen_hash,
        timestamp=ts,
        width=80,
        height=24,
    )


def _make_delta(prev: str, curr: str, count: int = 5, ts: float = 0.0) -> DeltaRef:
    return DeltaRef(
        previous_hash=prev,
        current_hash=curr,
        changed_cell_count=count,
        timestamp=ts,
    )


def test_push_and_len():
    buf = TuiObservationBuffer(max_snapshots=5)
    assert len(buf) == 0
    buf.push_snapshot(_make_snapshot("f1", "h1"))
    assert len(buf) == 1


def test_ringbuffer_overflow():
    """Nach max_snapshots Einträgen werden alte Snapshots verdrängt."""
    buf = TuiObservationBuffer(max_snapshots=3)
    for i in range(5):
        buf.push_snapshot(_make_snapshot(f"f{i}", f"h{i}", ts=float(i)))
    assert len(buf) == 3
    # Älteste (f0, f1) sollten nicht mehr drin sein
    assert buf.get_by_frame("f0") is None
    assert buf.get_by_frame("f1") is None
    # Neueste (f2, f3, f4) sind drin
    assert buf.get_by_frame("f2") is not None
    assert buf.get_by_frame("f4") is not None


def test_by_hash_lookup():
    buf = TuiObservationBuffer()
    snap = _make_snapshot("frame1", "hash_abc")
    buf.push_snapshot(snap)
    found = buf.get_by_hash("hash_abc")
    assert found is not None
    assert found.frame_id == "frame1"


def test_by_frame_lookup():
    buf = TuiObservationBuffer()
    snap = _make_snapshot("frame42", "h42")
    buf.push_snapshot(snap)
    found = buf.get_by_frame("frame42")
    assert found is not None
    assert found.screen_hash == "h42"


def test_evicted_snapshot_removed_from_lookup():
    """Verdrängter Snapshot ist aus _by_hash und _by_frame entfernt."""
    buf = TuiObservationBuffer(max_snapshots=2)
    buf.push_snapshot(_make_snapshot("f0", "h0"))
    buf.push_snapshot(_make_snapshot("f1", "h1"))
    buf.push_snapshot(_make_snapshot("f2", "h2"))  # verdrängt f0
    assert buf.get_by_hash("h0") is None
    assert buf.get_by_frame("f0") is None
    assert buf.get_by_hash("h1") is not None
    assert buf.get_by_hash("h2") is not None


def test_latest_snapshots_order():
    """latest_snapshots gibt die n neuesten in Einfügereihenfolge zurück."""
    buf = TuiObservationBuffer(max_snapshots=10)
    for i in range(6):
        buf.push_snapshot(_make_snapshot(f"f{i}", f"h{i}", ts=float(i)))
    latest = buf.latest_snapshots(3)
    assert len(latest) == 3
    assert latest[-1].frame_id == "f5"
    assert latest[-2].frame_id == "f4"
    assert latest[-3].frame_id == "f3"


def test_latest_snapshots_fewer_than_n():
    """latest_snapshots wenn weniger als n Snapshots vorhanden."""
    buf = TuiObservationBuffer()
    buf.push_snapshot(_make_snapshot("f0", "h0"))
    latest = buf.latest_snapshots(5)
    assert len(latest) == 1


def test_snapshots_in_window():
    """snapshots_in_window filtert korrekt nach Zeitstempel."""
    buf = TuiObservationBuffer()
    buf.push_snapshot(_make_snapshot("f0", "h0", ts=1.0))
    buf.push_snapshot(_make_snapshot("f1", "h1", ts=2.0))
    buf.push_snapshot(_make_snapshot("f2", "h2", ts=3.0))
    buf.push_snapshot(_make_snapshot("f3", "h3", ts=4.0))
    result = buf.snapshots_in_window(2.0, 3.0)
    frame_ids = [s.frame_id for s in result]
    assert "f1" in frame_ids
    assert "f2" in frame_ids
    assert "f0" not in frame_ids
    assert "f3" not in frame_ids


def test_snapshots_in_window_empty():
    buf = TuiObservationBuffer()
    buf.push_snapshot(_make_snapshot("f0", "h0", ts=10.0))
    result = buf.snapshots_in_window(1.0, 5.0)
    assert result == []


def test_push_delta():
    buf = TuiObservationBuffer()
    delta = _make_delta("h0", "h1", count=42)
    buf.push_delta(delta)
    pack = buf.llm_observation_pack()
    assert pack["delta_count"] == 1


def test_llm_observation_pack_structure():
    """llm_observation_pack gibt kompaktes Pack zurück ohne riesige Payloads."""
    buf = TuiObservationBuffer()
    for i in range(10):
        buf.push_snapshot(_make_snapshot(f"f{i}", f"h{i}", ts=float(i)))
        if i > 0:
            buf.push_delta(_make_delta(f"h{i-1}", f"h{i}", count=i * 2, ts=float(i)))
    pack = buf.llm_observation_pack(n_snapshots=3)
    assert pack["snapshot_count"] == 10
    assert pack["delta_count"] == 9
    assert len(pack["recent_snapshots"]) == 3
    assert len(pack["recent_deltas"]) <= 10
    # Kein cells_summary in recent_snapshots (nur kompakte Felder)
    for snap_dict in pack["recent_snapshots"]:
        assert "cells_summary" not in snap_dict
        assert "frame_id" in snap_dict
        assert "screen_hash" in snap_dict


def test_llm_observation_pack_no_huge_payload():
    """llm_observation_pack überschreitet nicht 10KB für normale Daten."""
    import json
    buf = TuiObservationBuffer(max_snapshots=20, max_deltas=100)
    for i in range(20):
        buf.push_snapshot(_make_snapshot(f"frame_{i:04d}", f"hash_{i:04x}", ts=float(i)))
        buf.push_delta(_make_delta(f"hash_{i:04x}", f"hash_{(i+1):04x}", count=i, ts=float(i)))
    pack = buf.llm_observation_pack(n_snapshots=3)
    payload = json.dumps(pack)
    assert len(payload) < 10_000, f"Payload zu groß: {len(payload)} bytes"


def test_deterministic_order():
    """Reihenfolge der Snapshots ist deterministisch (FIFO-Ringbuffer)."""
    buf = TuiObservationBuffer(max_snapshots=5)
    for i in range(5):
        buf.push_snapshot(_make_snapshot(f"f{i}", f"h{i}", ts=float(i)))
    latest = buf.latest_snapshots(5)
    frame_ids = [s.frame_id for s in latest]
    assert frame_ids == ["f0", "f1", "f2", "f3", "f4"]


def test_delta_ringbuffer():
    """Delta-Ringbuffer hält nur max_deltas Einträge."""
    buf = TuiObservationBuffer(max_deltas=3)
    for i in range(5):
        buf.push_delta(_make_delta(f"h{i}", f"h{i+1}"))
    pack = buf.llm_observation_pack()
    assert pack["delta_count"] == 3
