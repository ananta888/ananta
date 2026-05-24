from __future__ import annotations

import pytest

from agent.services.embedded_tool_session_service import (
    REASON_INVALID_TARGET,
    REASON_LAUNCH_FAILED,
    REASON_PATH_INVALID,
    REASON_PERMISSION_DENIED,
    REASON_UNKNOWN_TOOL,
    SESSION_TYPE_EDITOR,
    SESSION_TYPE_TOOL,
    TARGET_HUB,
    TARGET_HUB_AS_WORKER,
    TARGET_WORKER,
    EmbeddedToolSessionService,
    TuiToolPolicy,
)
from agent.services.tui_tool_registry import TuiToolRegistry, _GLOBAL_DEFAULT_CONFIG


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeSession:
    """Minimal ManagedLiveTerminalSession stand-in."""
    def __init__(self):
        self.calls: list[dict] = []
        self.closed = False

    def run_foreground_command(self, argv, *, timeout, cwd=None, reset_output=True):
        self.calls.append({"argv": argv, "cwd": cwd})
        return 0, "", ""


class _FakeSessionService:
    def __init__(self, *, raises=False):
        self._raises = raises
        self.sessions: dict[str, _FakeSession] = {}

    def ensure_session(self, session_id, *, start=True):
        if self._raises:
            raise RuntimeError("fake_launch_error")
        if session_id not in self.sessions:
            self.sessions[session_id] = _FakeSession()
        return self.sessions[session_id]

    def close_session(self, session_id):
        self.sessions.pop(session_id, None)


def _make_service(tmp_path=None, *, raises=False):
    import json, pathlib, tempfile
    td = tmp_path or pathlib.Path(tempfile.mkdtemp())
    raw = dict(_GLOBAL_DEFAULT_CONFIG)
    (td / "tui-tools.json").write_text(json.dumps(raw))
    registry = TuiToolRegistry(
        user_config_path=str(td / "none.json"),
        project_config_path=str(td / "tui-tools.json"),
    )
    fake_svc = _FakeSessionService(raises=raises)
    return EmbeddedToolSessionService(
        session_service=fake_svc,
        registry=registry,
    ), fake_svc, td


# ── launch_editor — happy path ────────────────────────────────────────────────

def test_launch_editor_creates_session(tmp_path):
    svc, fake, _ = _make_service(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("x")
    result = svc.launch_editor("s1", str(target), workspace=str(tmp_path))
    assert result.ok is True
    assert result.reason == "ok"
    assert result.meta.session_type == SESSION_TYPE_EDITOR
    assert result.meta.editor_id == "vim"


def test_launch_editor_metadata_stored(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "README.md"
    target.write_text("x")
    svc.launch_editor("s2", str(target), workspace=str(tmp_path))
    meta = svc.get_session_meta("s2")
    assert meta is not None
    assert meta.session_type == SESSION_TYPE_EDITOR
    assert meta.workspace == str(tmp_path)


def test_launch_editor_readonly_flag_passed_to_argv(tmp_path):
    svc, fake, _ = _make_service(tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("x")
    svc.launch_editor("s3", str(target), workspace=str(tmp_path), readonly=True)
    argv = fake.sessions["s3"].calls[0]["argv"]
    assert "-R" in argv  # vim readonly flag


def test_launch_editor_readonly_metadata(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.txt"
    target.write_text("x")
    svc.launch_editor("s4", str(target), workspace=str(tmp_path), readonly=True)
    assert svc.get_session_meta("s4").readonly is True


def test_launch_editor_with_explicit_editor(tmp_path):
    svc, fake, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    result = svc.launch_editor("s5", str(target), workspace=str(tmp_path), with_editor="nvim")
    assert result.ok is True
    assert result.meta.editor_id == "nvim"
    assert fake.sessions["s5"].calls[0]["argv"][0] == "nvim"


# ── launch_editor — policy enforcement ───────────────────────────────────────

def test_launch_editor_hub_denied_by_default(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    result = svc.launch_editor("s6", str(target), workspace=str(tmp_path), target_type=TARGET_HUB)
    assert result.ok is False
    assert result.reason == REASON_PERMISSION_DENIED


def test_launch_editor_hub_as_worker_denied_by_default(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    result = svc.launch_editor("s7", str(target), workspace=str(tmp_path), target_type=TARGET_HUB_AS_WORKER)
    assert result.ok is False
    assert result.reason == REASON_PERMISSION_DENIED


def test_launch_editor_hub_allowed_with_explicit_policy(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    policy = TuiToolPolicy(hub_tools_enabled=True)
    result = svc.launch_editor("s8", str(target), workspace=str(tmp_path), target_type=TARGET_HUB, policy=policy)
    assert result.ok is True


def test_launch_editor_write_denied_by_policy(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    policy = TuiToolPolicy(allow_write_editor=False)
    result = svc.launch_editor("s9", str(target), workspace=str(tmp_path), policy=policy, readonly=False)
    assert result.ok is False
    assert result.reason == REASON_PERMISSION_DENIED


def test_launch_editor_readonly_denied_by_policy(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    policy = TuiToolPolicy(allow_readonly_editor=False)
    result = svc.launch_editor("s10", str(target), workspace=str(tmp_path), readonly=True, policy=policy)
    assert result.ok is False
    assert result.reason == REASON_PERMISSION_DENIED


def test_launch_editor_invalid_target_rejected(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    result = svc.launch_editor("s11", str(target), workspace=str(tmp_path), target_type="unknown_target")
    assert result.ok is False
    assert result.reason == REASON_INVALID_TARGET


# ── launch_editor — path validation ──────────────────────────────────────────

def test_launch_editor_rejects_path_outside_workspace(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    result = svc.launch_editor("s12", "/etc/passwd", workspace=str(tmp_path))
    assert result.ok is False
    assert result.reason == REASON_PATH_INVALID


def test_launch_editor_rejects_path_traversal(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    result = svc.launch_editor("s13", "../../secret", workspace=str(tmp_path))
    assert result.ok is False
    assert result.reason == REASON_PATH_INVALID


# ── launch_editor — launch failure ───────────────────────────────────────────

def test_launch_editor_reports_launch_failure(tmp_path):
    svc, _, _ = _make_service(tmp_path, raises=True)
    target = tmp_path / "f.py"
    target.write_text("x")
    result = svc.launch_editor("s14", str(target), workspace=str(tmp_path))
    assert result.ok is False
    assert result.reason == REASON_LAUNCH_FAILED


# ── launch_tool — happy path ──────────────────────────────────────────────────

def test_launch_tool_creates_session(tmp_path):
    svc, fake, _ = _make_service(tmp_path)
    result = svc.launch_tool("t1", "git_ui", workspace=str(tmp_path))
    assert result.ok is True
    assert result.meta.session_type == SESSION_TYPE_TOOL
    assert result.meta.tool_id == "git_ui"


def test_launch_tool_argv_uses_lazygit(tmp_path):
    svc, fake, _ = _make_service(tmp_path)
    svc.launch_tool("t2", "git_ui", workspace=str(tmp_path))
    argv = fake.sessions["t2"].calls[0]["argv"]
    assert argv[0] == "lazygit"


def test_launch_tool_metadata_stored(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    svc.launch_tool("t3", "file_manager", workspace=str(tmp_path))
    meta = svc.get_session_meta("t3")
    assert meta is not None
    assert meta.tool_id == "file_manager"


# ── launch_tool — policy enforcement ─────────────────────────────────────────

def test_launch_tool_hub_denied_by_default(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    result = svc.launch_tool("t4", "git_ui", workspace=str(tmp_path), target_type=TARGET_HUB)
    assert result.ok is False
    assert result.reason == REASON_PERMISSION_DENIED


def test_launch_tool_worker_allowed_by_default(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    result = svc.launch_tool("t5", "git_ui", workspace=str(tmp_path), target_type=TARGET_WORKER)
    assert result.ok is True


def test_launch_tool_unknown_tool_rejected(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    result = svc.launch_tool("t6", "nonexistent_tool", workspace=str(tmp_path))
    assert result.ok is False
    assert result.reason == REASON_UNKNOWN_TOOL


# ── close and list ────────────────────────────────────────────────────────────

def test_close_removes_metadata(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    svc.launch_editor("s20", str(target), workspace=str(tmp_path))
    assert svc.get_session_meta("s20") is not None
    svc.close_embedded_session("s20")
    assert svc.get_session_meta("s20") is None


def test_list_embedded_sessions(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    svc.launch_editor("ls1", str(target), workspace=str(tmp_path))
    svc.launch_tool("ls2", "git_ui", workspace=str(tmp_path))
    sessions = svc.list_embedded_sessions()
    ids = {s.session_id for s in sessions}
    assert "ls1" in ids
    assert "ls2" in ids


def test_meta_to_dict(tmp_path):
    svc, _, _ = _make_service(tmp_path)
    target = tmp_path / "f.py"
    target.write_text("x")
    svc.launch_editor("s30", str(target), workspace=str(tmp_path))
    d = svc.get_session_meta("s30").to_dict()
    assert d["session_type"] == SESSION_TYPE_EDITOR
    assert d["editor_id"] == "vim"
    assert "launched_at" in d
