from __future__ import annotations

import pytest

from agent.tui_contract import ShellMode
from agent.tui_main_pane import TuiMainPaneController
from agent.tui_shell_runtime import TuiShellRuntime
from agent.services.embedded_tool_session_service import (
    EmbeddedSessionMeta,
    EmbeddedSessionResult,
    EmbeddedToolSessionService,
    SESSION_TYPE_EDITOR,
    SESSION_TYPE_TOOL,
    TuiToolPolicy,
)


# ── Stubs ─────────────────────────────────────────────────────────────────────

def _meta(session_id, session_type, *, editor_id="vim", tool_id="", file_path="", readonly=False):
    import time
    return EmbeddedSessionMeta(
        session_id=session_id,
        session_type=session_type,
        target_type="worker",
        workspace="/ws",
        file_path=file_path,
        editor_id=editor_id,
        readonly=readonly,
        tool_id=tool_id,
        launched_at=time.time(),
    )


class _StubEmbeddedService:
    def __init__(self, *, fail=False, fail_reason="permission_denied"):
        self.fail = fail
        self.fail_reason = fail_reason
        self.editor_calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def launch_editor(self, session_id, file_path, *, workspace, **kwargs) -> EmbeddedSessionResult:
        self.editor_calls.append({"session_id": session_id, "file_path": file_path, **kwargs})
        if self.fail:
            return EmbeddedSessionResult.failure(session_id, self.fail_reason)
        meta = _meta(session_id, SESSION_TYPE_EDITOR, file_path=file_path)
        return EmbeddedSessionResult.success(session_id, meta)

    def launch_tool(self, session_id, tool_id, *, workspace, **kwargs) -> EmbeddedSessionResult:
        self.tool_calls.append({"session_id": session_id, "tool_id": tool_id, **kwargs})
        if self.fail:
            return EmbeddedSessionResult.failure(session_id, self.fail_reason)
        meta = _meta(session_id, SESSION_TYPE_TOOL, tool_id=tool_id)
        return EmbeddedSessionResult.success(session_id, meta)


def _make_controller(*, fail=False, fail_reason="permission_denied"):
    runtime = TuiShellRuntime()
    stub = _StubEmbeddedService(fail=fail, fail_reason=fail_reason)
    return TuiMainPaneController(runtime=runtime, embedded_service=stub), runtime, stub


# ── open_file ─────────────────────────────────────────────────────────────────

def test_open_file_switches_to_embedded_editor():
    ctrl, runtime, _ = _make_controller()
    result = ctrl.open_file("/ws/app.py", "/ws", session_id="s1")
    assert result["ok"] is True
    assert runtime.current_state().mode == ShellMode.EMBEDDED_EDITOR


def test_open_file_sets_session_id_in_state():
    ctrl, runtime, _ = _make_controller()
    ctrl.open_file("/ws/app.py", "/ws", session_id="s1")
    assert runtime.current_state().session_id == "s1"


def test_open_file_sets_file_path_in_state():
    ctrl, runtime, _ = _make_controller()
    ctrl.open_file("/ws/notes.txt", "/ws", session_id="s2")
    assert runtime.current_state().file_path == "/ws/notes.txt"


def test_open_file_readonly_flag_propagated():
    ctrl, runtime, stub = _make_controller()
    ctrl.open_file("/ws/f.py", "/ws", session_id="s3", readonly=True)
    assert stub.editor_calls[0]["readonly"] is True
    assert runtime.current_state().read_only is True


def test_open_file_with_editor_propagated():
    ctrl, runtime, stub = _make_controller()
    ctrl.open_file("/ws/f.py", "/ws", session_id="s4", with_editor="nvim")
    assert stub.editor_calls[0]["with_editor"] == "nvim"


def test_open_file_autogenerates_session_id():
    ctrl, runtime, _ = _make_controller()
    result = ctrl.open_file("/ws/f.py", "/ws")
    assert result["session_id"].startswith("tui-")


def test_open_file_returns_editor_id():
    ctrl, _, _ = _make_controller()
    result = ctrl.open_file("/ws/f.py", "/ws", session_id="s5")
    assert result["editor_id"] == "vim"


def test_open_file_failure_returns_ok_false():
    ctrl, runtime, _ = _make_controller(fail=True, fail_reason="path_invalid")
    result = ctrl.open_file("/outside", "/ws", session_id="s6")
    assert result["ok"] is False
    assert result["reason"] == "path_invalid"
    # Mode stays in dashboard on failure
    assert runtime.current_state().mode == ShellMode.DASHBOARD


# ── launch_tool ───────────────────────────────────────────────────────────────

def test_launch_tool_switches_to_embedded_tool():
    ctrl, runtime, _ = _make_controller()
    result = ctrl.launch_tool("git_ui", "/ws", session_id="t1")
    assert result["ok"] is True
    assert runtime.current_state().mode == ShellMode.EMBEDDED_TOOL


def test_launch_tool_sets_tool_id_in_state():
    ctrl, runtime, _ = _make_controller()
    ctrl.launch_tool("git_ui", "/ws", session_id="t2")
    assert runtime.current_state().tool_id == "git_ui"


def test_launch_tool_autogenerates_session_id():
    ctrl, _, _ = _make_controller()
    result = ctrl.launch_tool("git_ui", "/ws")
    assert result["session_id"].startswith("tui-")


def test_launch_tool_failure_stays_in_dashboard():
    ctrl, runtime, _ = _make_controller(fail=True, fail_reason="unknown_tool")
    result = ctrl.launch_tool("badtool", "/ws", session_id="t3")
    assert result["ok"] is False
    assert result["reason"] == "unknown_tool"
    assert runtime.current_state().mode == ShellMode.DASHBOARD


# ── return_to_dashboard ───────────────────────────────────────────────────────

def test_return_to_dashboard_from_editor():
    ctrl, runtime, _ = _make_controller()
    ctrl.open_file("/ws/f.py", "/ws", session_id="s7")
    assert runtime.current_state().mode == ShellMode.EMBEDDED_EDITOR
    result = ctrl.return_to_dashboard()
    assert result["ok"] is True
    assert runtime.current_state().mode == ShellMode.DASHBOARD


def test_return_to_dashboard_from_tool():
    ctrl, runtime, _ = _make_controller()
    ctrl.launch_tool("git_ui", "/ws", session_id="t4")
    ctrl.return_to_dashboard()
    assert runtime.current_state().mode == ShellMode.DASHBOARD


def test_return_to_dashboard_from_dashboard_is_noop():
    ctrl, runtime, _ = _make_controller()
    result = ctrl.return_to_dashboard()
    assert result["ok"] is True
    assert runtime.current_state().mode == ShellMode.DASHBOARD


# ── status ────────────────────────────────────────────────────────────────────

def test_status_shows_dashboard_initially():
    ctrl, _, _ = _make_controller()
    s = ctrl.status()
    assert s["mode"] == "dashboard"


def test_status_reflects_current_mode():
    ctrl, _, _ = _make_controller()
    ctrl.open_file("/ws/f.py", "/ws", session_id="s8")
    s = ctrl.status()
    assert s["mode"] == "embedded_editor"
    assert s["session_id"] == "s8"


def test_current_mode_returns_enum():
    ctrl, _, _ = _make_controller()
    assert ctrl.current_mode() == ShellMode.DASHBOARD


# ── full flow ─────────────────────────────────────────────────────────────────

def test_full_flow_open_return_reopen():
    ctrl, runtime, _ = _make_controller()
    ctrl.open_file("/ws/a.py", "/ws", session_id="s9")
    assert runtime.current_state().mode == ShellMode.EMBEDDED_EDITOR
    ctrl.return_to_dashboard()
    assert runtime.current_state().mode == ShellMode.DASHBOARD
    ctrl.open_file("/ws/b.py", "/ws", session_id="s10")
    assert runtime.current_state().mode == ShellMode.EMBEDDED_EDITOR
    assert runtime.current_state().session_id == "s10"
