from __future__ import annotations

import builtins
import types

from agent.cli import main as cli_main


def test_tui_command_delegates_to_existing_entrypoint(monkeypatch) -> None:
    fake_module = types.ModuleType("client_surfaces.tui_runtime.ananta_tui.app")
    fake_module.main = lambda argv=None: 0  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "client_surfaces.tui_runtime.ananta_tui.app", fake_module)

    rc = cli_main.main(["tui", "--fixture"])

    assert rc == 0


def test_tui_command_degrades_when_launcher_missing(monkeypatch, capsys) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == "client_surfaces.tui_runtime.ananta_tui.app":
            raise ModuleNotFoundError("missing tui runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    rc = cli_main.main(["tui", "--fixture"])

    out = capsys.readouterr().out
    assert rc == 2
    assert "TUI launcher is unavailable" in out


def test_web_command_supports_help(capsys) -> None:
    rc = cli_main.main(["web", "--help"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage: ananta web" in out


def test_web_command_prints_env_url(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ANANTA_WEB_URL", "http://web.example:4300")

    rc = cli_main.main(["web"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "http://web.example:4300" in out


def test_web_command_accepts_explicit_url(capsys) -> None:
    rc = cli_main.main(["web", "--url", "http://localhost:4400"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "http://localhost:4400" in out


def test_web_command_rejects_invalid_args(capsys) -> None:
    rc = cli_main.main(["web", "--bad-flag"])

    out = capsys.readouterr().out
    assert rc == 2
    assert "only supports optional `--url <web-url>`" in out
