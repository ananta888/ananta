from __future__ import annotations

import pytest

from agent.tui_contract import ShellMode, TuiPaneState
from agent.tui_shell_runtime import (
    DEFAULT_SUSPEND_KEY,
    ShellModeTransitionError,
    TuiShellRuntime,
    KeyDispatchResult,
)


# ── Initial state ─────────────────────────────────────────────────────────────

def test_initial_mode_is_dashboard():
    rt = TuiShellRuntime()
    assert rt.current_state().mode == ShellMode.DASHBOARD


def test_initial_state_is_frozen():
    rt = TuiShellRuntime()
    state = rt.current_state()
    assert isinstance(state, TuiPaneState)
    assert state.session_id == ""


# ── switch_to: valid transitions ─────────────────────────────────────────────

def test_dashboard_to_embedded_editor():
    rt = TuiShellRuntime()
    state = rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1", "file_path": "/ws/f.py"})
    assert state.mode == ShellMode.EMBEDDED_EDITOR
    assert state.session_id == "s1"
    assert state.file_path == "/ws/f.py"


def test_dashboard_to_terminal_session():
    rt = TuiShellRuntime()
    state = rt.switch_to(ShellMode.TERMINAL_SESSION, {"session_id": "t1"})
    assert state.mode == ShellMode.TERMINAL_SESSION


def test_dashboard_to_embedded_tool():
    rt = TuiShellRuntime()
    state = rt.switch_to(ShellMode.EMBEDDED_TOOL, {"tool_id": "git_ui"})
    assert state.mode == ShellMode.EMBEDDED_TOOL
    assert state.tool_id == "git_ui"


def test_embedded_editor_back_to_dashboard():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    state = rt.switch_to(ShellMode.DASHBOARD)
    assert state.mode == ShellMode.DASHBOARD


def test_embedded_tool_to_embedded_editor():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_TOOL, {"tool_id": "git_ui"})
    state = rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s2", "file_path": "/f"})
    assert state.mode == ShellMode.EMBEDDED_EDITOR


def test_context_defaults_to_empty_strings():
    rt = TuiShellRuntime()
    state = rt.switch_to(ShellMode.EMBEDDED_EDITOR)
    assert state.session_id == ""
    assert state.file_path == ""
    assert state.read_only is False


def test_readonly_context_set():
    rt = TuiShellRuntime()
    state = rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"read_only": True, "session_id": "s1"})
    assert state.read_only is True


# ── switch_to: invalid transitions ───────────────────────────────────────────

def test_dashboard_cannot_transition_to_itself():
    rt = TuiShellRuntime()
    with pytest.raises(ShellModeTransitionError):
        rt.switch_to(ShellMode.DASHBOARD)


def test_invalid_transition_raises():
    rt = TuiShellRuntime()
    # DASHBOARD → DASHBOARD is invalid per _VALID_TRANSITIONS
    with pytest.raises(ShellModeTransitionError, match="Invalid transition"):
        rt.switch_to(ShellMode.DASHBOARD)


# ── state_change callback ─────────────────────────────────────────────────────

def test_on_state_change_called_on_transition():
    received = []
    rt = TuiShellRuntime(on_state_change=received.append)
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    assert len(received) == 1
    assert received[0].mode == ShellMode.EMBEDDED_EDITOR


def test_on_state_change_not_called_on_failed_transition():
    received = []
    rt = TuiShellRuntime(on_state_change=received.append)
    with pytest.raises(ShellModeTransitionError):
        rt.switch_to(ShellMode.DASHBOARD)  # invalid
    assert len(received) == 0


# ── suspend ───────────────────────────────────────────────────────────────────

def test_suspend_from_embedded_editor_returns_dashboard():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    state = rt.suspend()
    assert state.mode == ShellMode.DASHBOARD


def test_suspend_from_dashboard_is_noop():
    rt = TuiShellRuntime()
    state = rt.suspend()
    assert state.mode == ShellMode.DASHBOARD


def test_suspend_does_not_raise_from_any_mode():
    for mode in (ShellMode.EMBEDDED_EDITOR, ShellMode.EMBEDDED_TOOL, ShellMode.TERMINAL_SESSION):
        rt = TuiShellRuntime()
        rt.switch_to(mode, {"session_id": "x"})
        state = rt.suspend()
        assert state.mode == ShellMode.DASHBOARD


# ── dispatch_key: forwarding ──────────────────────────────────────────────────

def test_keys_forwarded_in_embedded_editor():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    result = rt.dispatch_key("a")
    assert result.forwarded is True
    assert result.consumed is False


def test_keys_forwarded_in_embedded_tool():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_TOOL, {"tool_id": "git_ui"})
    result = rt.dispatch_key("j")
    assert result.forwarded is True


def test_keys_consumed_in_dashboard():
    rt = TuiShellRuntime()
    result = rt.dispatch_key("j")
    assert result.consumed is True
    assert result.forwarded is False


def test_keys_consumed_in_terminal_session():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.TERMINAL_SESSION, {"session_id": "t1"})
    result = rt.dispatch_key("k")
    assert result.consumed is True


# ── dispatch_key: suspend chord ───────────────────────────────────────────────

def test_suspend_chord_triggers_suspend_in_embedded_mode():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    # Send first char of chord
    r1 = rt.dispatch_key(DEFAULT_SUSPEND_KEY[0])
    assert r1.consumed is True
    assert r1.suspend_triggered is False
    # Send second char
    r2 = rt.dispatch_key(DEFAULT_SUSPEND_KEY[1])
    assert r2.suspend_triggered is True
    assert rt.current_state().mode == ShellMode.DASHBOARD


def test_suspend_chord_mid_sequence_non_chord_key_resets():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    rt.dispatch_key(DEFAULT_SUSPEND_KEY[0])  # first char
    # Send unrelated key — chord resets, key forwarded normally
    result = rt.dispatch_key("x")
    assert result.suspend_triggered is False
    # Mode unchanged — not suspended
    assert rt.current_state().mode == ShellMode.EMBEDDED_EDITOR


def test_custom_suspend_key():
    rt = TuiShellRuntime(suspend_key="\x18")  # Ctrl+X
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    result = rt.dispatch_key("\x18")
    assert result.suspend_triggered is True
    assert rt.current_state().mode == ShellMode.DASHBOARD


# ── TuiPaneState helpers ──────────────────────────────────────────────────────

def test_pane_state_is_embedded_true():
    state = TuiPaneState(mode=ShellMode.EMBEDDED_EDITOR)
    assert state.is_embedded() is True


def test_pane_state_is_embedded_false_for_dashboard():
    state = TuiPaneState(mode=ShellMode.DASHBOARD)
    assert state.is_embedded() is False


def test_pane_state_to_dict():
    state = TuiPaneState(mode=ShellMode.EMBEDDED_TOOL, tool_id="git_ui", target_type="worker")
    d = state.to_dict()
    assert d["mode"] == "embedded_tool"
    assert d["tool_id"] == "git_ui"
    assert d["target_type"] == "worker"


# ── reset ─────────────────────────────────────────────────────────────────────

def test_reset_returns_to_dashboard():
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    rt.reset()
    assert rt.current_state().mode == ShellMode.DASHBOARD


# ── thread safety ─────────────────────────────────────────────────────────────

def test_concurrent_state_reads_are_consistent():
    import threading
    rt = TuiShellRuntime()
    rt.switch_to(ShellMode.EMBEDDED_EDITOR, {"session_id": "s1"})
    results = []
    def read():
        results.append(rt.current_state().mode)
    threads = [threading.Thread(target=read) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(m == ShellMode.EMBEDDED_EDITOR for m in results)
