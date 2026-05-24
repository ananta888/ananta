from __future__ import annotations

import subprocess

import pytest

from agent.services.tmux_backend import TmuxBackendError, TmuxSessionBackend


def test_tmux_backend_missing_binary_raises_clear_error(monkeypatch):
    backend = TmuxSessionBackend()
    monkeypatch.setattr("agent.services.tmux_backend.shutil.which", lambda _name: None)
    with pytest.raises(TmuxBackendError, match="tmux_binary_missing"):
        backend.create_session(name_hint="worker", cwd=None)


def test_tmux_backend_normalizes_name_and_avoids_shell(monkeypatch):
    backend = TmuxSessionBackend()
    monkeypatch.setattr("agent.services.tmux_backend.shutil.which", lambda _name: "/usr/bin/tmux")

    calls = []

    def fake_run(argv, capture_output=True, text=True):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("agent.services.tmux_backend.subprocess.run", fake_run)
    session = backend.create_session(name_hint="bad;name && rm -rf /", cwd="/tmp")

    assert session.tmux_session_name.startswith("bad-name-rm-rf")
    assert calls[0][0:3] == ["/usr/bin/tmux", "new-session", "-d"]


def test_tmux_backend_send_and_kill_target_expected_session(monkeypatch):
    backend = TmuxSessionBackend()
    monkeypatch.setattr("agent.services.tmux_backend.shutil.which", lambda _name: "/usr/bin/tmux")
    calls = []

    def fake_run(argv, capture_output=True, text=True):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr("agent.services.tmux_backend.subprocess.run", fake_run)
    backend.send_input(session_name="ananta-safe-1", text="ls")
    backend.kill_session(session_name="ananta-safe-1")

    assert calls[0] == ["/usr/bin/tmux", "send-keys", "-t", "ananta-safe-1:0.0", "ls", "C-m"]
    assert calls[1] == ["/usr/bin/tmux", "kill-session", "-t", "ananta-safe-1"]
