from __future__ import annotations

from client_surfaces.operator_tui.shared_viewer import (
    SharedViewer,
    is_viewer_action_blocked,
    render_shared_viewer_lines,
)


def test_shared_viewer_header_and_read_only_label():
    viewer = SharedViewer(session_id="sess-1", owner_id="owner-user")
    lines = render_shared_viewer_lines(viewer.state, width=100, height=20)
    assert "READ ONLY VIEW OF owner-user" in lines[0]
    assert "[WAITING]" in lines[0]


def test_shared_viewer_keeps_last_snapshot_when_disconnected():
    viewer = SharedViewer(session_id="sess-2", owner_id="owner-user")
    viewer.apply_frame("line-1\nline-2", "h1")
    viewer.mark_disconnected()
    assert viewer.state.is_disconnected
    assert viewer.state.is_stale
    assert viewer.state.current_text == "line-1\nline-2"
    lines = render_shared_viewer_lines(viewer.state, width=80, height=8)
    assert "line-1" in "\n".join(lines)
    assert "DISCONNECTED" in lines[0]


def test_shared_viewer_blocks_mutating_actions():
    assert is_viewer_action_blocked("goal_create")
    assert is_viewer_action_blocked("execute")
    assert not is_viewer_action_blocked("scroll_down")
