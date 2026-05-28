"""E2E: TUI-Snapshot + Delta Pipeline.

T08.01: Verifikation dass CellGrid-Snapshots und Deltas korrekt erzeugt werden.
Läuft ohne LM Studio (kein ANANTA_E2E_LIVE_LMSTUDIO nötig).
"""
import pytest
from client_surfaces.operator_tui.snapshot import CellGrid
from client_surfaces.operator_tui.snapshot_delta import DeltaEncoder, TuiDelta
from client_surfaces.operator_tui.ansi_replay import AnsiReplayState


def test_snapshot_from_rendered_lines_has_correct_dimensions():
    lines = ["Hello World", "Second Line", "Third Line "]
    grid = CellGrid.from_rendered_lines(lines)
    assert grid.height == 3
    assert grid.width == len("Hello World")
    assert grid.screen_hash


def test_snapshot_hash_is_deterministic():
    lines = ["foo bar", "baz qux"]
    g1 = CellGrid.from_rendered_lines(lines)
    g2 = CellGrid.from_rendered_lines(lines)
    assert g1.screen_hash == g2.screen_hash


def test_delta_reconstruction_matches_current_snapshot():
    """Delta auf Basis-Snapshot angewendet ergibt aktuellen Snapshot."""
    lines_a = ["AAAA BBBB", "CCCC DDDD"]
    lines_b = ["AAAA XXXX", "CCCC DDDD"]
    grid_a = CellGrid.from_rendered_lines(lines_a)
    grid_b = CellGrid.from_rendered_lines(lines_b)

    encoder = DeltaEncoder()
    delta = encoder.encode(grid_a, grid_b)

    assert delta.changed_cell_count > 0
    assert delta.previous_hash == grid_a.screen_hash
    assert delta.current_hash == grid_b.screen_hash

    reconstructed = encoder.apply(grid_a, delta)
    assert reconstructed.screen_hash == grid_b.screen_hash


def test_unchanged_screen_yields_empty_delta():
    lines = ["Same Line", "Also Same"]
    g = CellGrid.from_rendered_lines(lines)
    encoder = DeltaEncoder()
    delta = encoder.encode(g, g)
    assert delta.changed_cell_count == 0


def test_ansi_replay_produces_valid_snapshot():
    state = AnsiReplayState(width=10, height=3)
    state.apply_chunk("Hello")
    grid = state.to_cell_grid()
    assert grid.width == 10
    assert grid.height == 3
    assert grid.cells[0][0].char == "H"


def test_snapshot_artifacts_contain_header_and_multiple_frames():
    """Verifies that multiple snapshots can be generated from successive renders."""
    from agent.services.heuristic_runtime.tui_observation_buffer import TuiObservationBuffer, SnapshotRef
    import time

    buf = TuiObservationBuffer(max_snapshots=10)
    for i in range(5):
        lines = [f"Frame {i} content here", "Static line below"]
        grid = CellGrid.from_rendered_lines(lines)
        ref = SnapshotRef(
            frame_id=f"frame_{i}",
            screen_hash=grid.screen_hash,
            timestamp=time.monotonic(),
            width=grid.width,
            height=grid.height,
        )
        buf.push_snapshot(ref)

    assert len(buf) == 5
    pack = buf.llm_observation_pack(n_snapshots=3)
    assert len(pack["recent_snapshots"]) == 3
    assert "snapshot_count" in pack
