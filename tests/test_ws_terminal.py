from types import SimpleNamespace

from agent import ws_terminal as ws_mod
from agent.services import terminal_bridge as bridge_mod


def test_build_terminal_bridge_uses_pipe_on_windows(monkeypatch):
    monkeypatch.setattr(bridge_mod, "os", SimpleNamespace(name="nt"))
    bridge = bridge_mod.build_terminal_bridge("cmd.exe")
    assert isinstance(bridge, bridge_mod.PipeBridge)


def test_safe_shell_windows_prefers_comspec(monkeypatch):
    monkeypatch.setattr(
        ws_mod,
        "os",
        SimpleNamespace(
            name="nt",
            environ={"COMSPEC": "cmd.exe"},
            path=SimpleNamespace(exists=lambda _path: False),
        ),
    )
    monkeypatch.setattr(ws_mod.settings, "shell_path", "")
    shell = ws_mod._safe_shell()
    assert shell == "cmd.exe"


def test_safe_shell_posix_default(monkeypatch):
    monkeypatch.setattr(
        ws_mod,
        "os",
        SimpleNamespace(name="posix", path=SimpleNamespace(exists=lambda _path: False)),
    )
    monkeypatch.setattr(ws_mod.settings, "shell_path", "/definitely/missing-shell")
    shell = ws_mod._safe_shell()
    assert shell == "/bin/sh"


def test_extract_terminal_input_accepts_explicit_input_payload():
    assert ws_mod._extract_terminal_input('{"type":"input","data":"ls\\n"}') == "ls\n"


def test_extract_terminal_input_ignores_non_input_json():
    assert ws_mod._extract_terminal_input('{"type":"output","data":{"chunk":"ignored"}}') is None


def test_extract_terminal_resize_accepts_explicit_resize_payload():
    assert ws_mod._extract_terminal_resize('{"type":"resize","cols":120,"rows":40}') == (120, 40)


def test_extract_terminal_resize_ignores_non_resize_payload():
    assert ws_mod._extract_terminal_resize('{"type":"input","data":"ls\\n"}') is None
    assert ws_mod._extract_terminal_resize('{"type":"resize","cols":"x","rows":40}') is None


def test_extract_terminal_input_preserves_raw_text():
    assert ws_mod._extract_terminal_input("echo hi\n") == "echo hi\n"
