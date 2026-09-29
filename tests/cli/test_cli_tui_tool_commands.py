from __future__ import annotations

import json
import pytest

from agent.cli.commands.tui_editor import dispatch as tui_dispatch
from agent.cli.commands.tmux import dispatch as tmux_dispatch
from agent.services.tui_tool_registry import TuiToolRegistry, _GLOBAL_DEFAULT_CONFIG


# ── Helpers ───────────────────────────────────────────────────────────────────

def _capture_exec():
    """Return a list that records exec calls instead of actually exec'ing."""
    calls = []
    def _fake_exec(argv):
        calls.append(argv)
    return calls, _fake_exec


# ── ananta tui --open ─────────────────────────────────────────────────────────

def test_tui_open_resolves_default_editor(tmp_path):
    target = tmp_path / "README.md"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", str(target), "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "vim"
    assert str(target) in calls[0]


def test_tui_open_with_nvim(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", str(target), "--with", "nvim", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "nvim"


def test_tui_open_readonly_passes_R_flag(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", str(target), "--readonly", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert "-R" in calls[0]


def test_tui_open_outside_workspace_returns_error(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", "/etc/passwd", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 2
    assert len(calls) == 0


def test_tui_open_path_traversal_returns_error(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", "../../secret", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 2
    assert len(calls) == 0


def test_tui_open_relative_path_resolved(tmp_path, monkeypatch):
    target = tmp_path / "local.py"
    target.write_text("x")
    monkeypatch.chdir(tmp_path)
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--open", "local.py"], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "vim"


# ── ananta tui --tool ─────────────────────────────────────────────────────────

def test_tui_tool_known_tool_executes(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--tool", "git_ui", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "lazygit"


def test_tui_tool_file_manager(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--tool", "file_manager", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "ranger"


def test_tui_tool_workspace_substituted(tmp_path):
    calls, fake_exec = _capture_exec()
    tui_dispatch(["--tool", "file_manager", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert str(tmp_path) in calls[0]


def test_tui_tool_unknown_tool_returns_error(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(["--tool", "nonexistent", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 2
    assert len(calls) == 0


# ── ananta tui: argument errors ───────────────────────────────────────────────

def test_tui_no_args_prints_help(capsys):
    rc = tui_dispatch([], _exec_fn=lambda argv: None)
    assert rc == 0
    captured = capsys.readouterr()
    assert "open" in captured.out.lower() or "tool" in captured.out.lower()


def test_tui_open_and_tool_mutually_exclusive(tmp_path):
    target = tmp_path / "f.py"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tui_dispatch(
        ["--open", str(target), "--tool", "git_ui", "--workspace", str(tmp_path)],
        _exec_fn=fake_exec,
    )
    assert rc == 2  # argparse mutual exclusion


# ── ananta tmux edit ─────────────────────────────────────────────────────────

def test_tmux_edit_resolves_editor(tmp_path):
    target = tmp_path / "README.md"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["edit", str(target), "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "vim"


def test_tmux_edit_with_nvim(tmp_path):
    target = tmp_path / "main.py"
    target.write_text("x")
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["edit", str(target), "--with", "nvim", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "nvim"


def test_tmux_edit_readonly(tmp_path):
    target = tmp_path / "cfg.json"
    target.write_text("{}")
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["edit", str(target), "--readonly", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert "-R" in calls[0]


def test_tmux_edit_outside_workspace_rejected(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["edit", "/etc/hosts", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 2
    assert len(calls) == 0


# ── ananta tmux tool ─────────────────────────────────────────────────────────

def test_tmux_tool_lazygit(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["tool", "git_ui", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 0
    assert calls[0][0] == "lazygit"


def test_tmux_tool_unknown_returns_error(tmp_path):
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["tool", "no_such_tool", "--workspace", str(tmp_path)], _exec_fn=fake_exec)
    assert rc == 2
    assert len(calls) == 0


def test_tmux_no_subcommand_prints_help(capsys):
    rc = tmux_dispatch([], _exec_fn=lambda argv: None)
    assert rc == 0


def test_tmux_invalid_subcommand_returns_error():
    calls, fake_exec = _capture_exec()
    rc = tmux_dispatch(["invalid_subcmd"], _exec_fn=fake_exec)
    assert rc == 2


# ── cli/main.py integration ───────────────────────────────────────────────────

def test_main_tui_open_dispatches_to_tui_editor(tmp_path, monkeypatch):
    """Verify cli/main.py routes ananta tui --open to tui_editor.dispatch."""
    target = tmp_path / "f.py"
    target.write_text("x")
    calls = []

    import agent.cli.commands.tui_editor as tui_editor_mod
    original = tui_editor_mod._default_exec

    monkeypatch.setattr(tui_editor_mod, "_default_exec", lambda argv: calls.append(argv))
    from agent.cli.main import main
    rc = main(["tui", "--open", str(target), "--workspace", str(tmp_path)])
    monkeypatch.setattr(tui_editor_mod, "_default_exec", original)

    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "vim"


def test_main_tmux_dispatches(tmp_path, monkeypatch):
    """Verify cli/main.py routes ananta tmux tool to tmux.dispatch."""
    calls = []
    import agent.cli.commands.tmux as tmux_mod
    original = tmux_mod._default_exec
    monkeypatch.setattr(tmux_mod, "_default_exec", lambda argv: calls.append(argv))
    from agent.cli.main import main
    rc = main(["tmux", "tool", "git_ui", "--workspace", str(tmp_path)])
    monkeypatch.setattr(tmux_mod, "_default_exec", original)
    assert rc == 0
    assert calls[0][0] == "lazygit"
