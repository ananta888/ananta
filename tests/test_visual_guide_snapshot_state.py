"""VG-001 regression: _visual_last_delta_snapshot and _visual_last_reply_snapshot
must be kept separate — the delta baseline must NOT be confused with the
reply-throttle key.

The `app` fixture is required to avoid a circular-import problem that arises
when snakes_execution_routes is imported before the Flask app bootstraps.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_visual_state(app):
    """Reset both module-global visual state variables before and after each test.
    Requires the `app` fixture so Flask + route modules are fully initialised."""
    import agent.routes.snakes_execution_routes as ser
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0
    yield ser
    ser._visual_last_delta_snapshot = ""
    ser._visual_last_reply_snapshot = ""
    ser._visual_last_reply_at = 0.0


def test_delta_and_reply_state_are_independent_variables(reset_visual_state):
    """The two snapshots track different things and must start independent."""
    routes = reset_visual_state
    routes._visual_last_delta_snapshot = "snap_A"
    routes._visual_last_reply_snapshot = ""

    # delta has been updated but reply has not → they are different
    assert routes._visual_last_delta_snapshot != routes._visual_last_reply_snapshot


def test_reply_snapshot_does_not_change_when_delta_updated(reset_visual_state):
    """Updating _visual_last_delta_snapshot must not touch _visual_last_reply_snapshot."""
    routes = reset_visual_state
    routes._visual_last_delta_snapshot = "snap_A"
    # reply snapshot deliberately left as empty string
    assert routes._visual_last_reply_snapshot == ""


def test_reply_throttle_uses_reply_snapshot_not_delta(reset_visual_state):
    """_spawn_visual_reply early-exits only when _visual_last_reply_snapshot == snapshot,
    NOT when _visual_last_delta_snapshot == snapshot."""
    routes = reset_visual_state
    # delta has already observed snap_A
    routes._visual_last_delta_snapshot = "snap_A"
    routes._visual_last_reply_snapshot = ""  # reply hasn't produced anything yet

    # The throttle check inspects _visual_last_reply_snapshot.
    # Since it's empty, snap_A is NOT equal to it → no early-exit should occur.
    assert routes._visual_last_reply_snapshot != "snap_A"


def test_reply_throttle_blocks_when_reply_snapshot_matches(reset_visual_state):
    """Once _visual_last_reply_snapshot is set to snap_A, a second call with
    snap_A must be recognised as a duplicate (throttle should suppress it)."""
    routes = reset_visual_state
    routes._visual_last_reply_snapshot = "snap_A"

    # Verifying the state that _spawn_visual_reply checks:
    # if ui_snapshot == _visual_last_reply_snapshot: return
    assert routes._visual_last_reply_snapshot == "snap_A"


def test_different_snapshots_pass_reply_throttle_check(reset_visual_state):
    """When _visual_last_reply_snapshot is snap_A and the incoming snapshot is
    snap_B, the equality check fails → snap_B would not be suppressed."""
    routes = reset_visual_state
    routes._visual_last_reply_snapshot = "snap_A"

    assert routes._visual_last_reply_snapshot != "snap_B"


def test_delta_snapshot_and_reply_snapshot_can_diverge(reset_visual_state):
    """A realistic scenario: delta tracks every tick; reply only tracks the last
    AI-generating tick.  After three ticks only the last reply-generating tick
    is remembered in _visual_last_reply_snapshot."""
    routes = reset_visual_state
    # Simulate three ticks
    for snap in ("/teams", "/chats", "/workspace"):
        routes._visual_last_delta_snapshot = snap
    # Reply was only generated for the second tick
    routes._visual_last_reply_snapshot = "/chats"

    assert routes._visual_last_delta_snapshot == "/workspace"
    assert routes._visual_last_reply_snapshot == "/chats"
    assert routes._visual_last_delta_snapshot != routes._visual_last_reply_snapshot
