import os

from agent import ws_terminal as ws_mod


def test_build_terminal_bridge_uses_pipe_on_windows(monkeypatch):
    monkeypatch.setattr(ws_mod.os, "name", "nt")
    bridge = ws_mod._build_terminal_bridge("cmd.exe")
    assert isinstance(bridge, ws_mod.PipeBridge)


def test_safe_shell_windows_prefers_comspec(monkeypatch):
    monkeypatch.setattr(ws_mod.os, "name", "nt")
    monkeypatch.setattr(ws_mod.settings, "shell_path", "")
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    shell = ws_mod._safe_shell()
    assert shell == "cmd.exe"


def test_safe_shell_posix_default(monkeypatch):
    monkeypatch.setattr(ws_mod.os, "name", "posix")
    monkeypatch.setattr(ws_mod.settings, "shell_path", "/definitely/missing-shell")
    shell = ws_mod._safe_shell()
    assert shell == "/bin/sh"

