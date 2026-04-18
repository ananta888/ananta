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


def test_auth_payload_is_admin_accepts_role_and_roles():
    assert ws_mod._auth_payload_is_admin({"role": "admin"}) is True
    assert ws_mod._auth_payload_is_admin({"roles": ["viewer", "admin"]}) is True
    assert ws_mod._auth_payload_is_admin({"role": "user"}) is False
    assert ws_mod._auth_payload_is_admin(None) is False


def test_auth_payload_roles_collects_single_role_and_roles():
    assert ws_mod._auth_payload_roles({"role": "admin", "roles": ["operator"]}) == ["admin", "operator"]
    assert ws_mod._auth_payload_roles(None) == []


def test_terminal_limit_reason_detects_max_duration_and_idle_timeout():
    policy = {"max_session_seconds": 60, "idle_timeout_seconds": 10}

    assert (
        ws_mod._terminal_limit_reason(policy=policy, started_at=0, last_activity_at=55, now=60)
        == "terminal_max_session_seconds_exceeded"
    )
    assert (
        ws_mod._terminal_limit_reason(policy=policy, started_at=0, last_activity_at=15, now=25)
        == "terminal_idle_timeout_seconds_exceeded"
    )
    assert ws_mod._terminal_limit_reason(policy=policy, started_at=0, last_activity_at=20, now=25) is None


def test_terminal_preview_limit_uses_policy_value():
    assert ws_mod._terminal_preview_limit({"input_preview_max_chars": 42}) == 42
    assert ws_mod._terminal_preview_limit({"input_preview_max_chars": "bad"}) == 120


def test_websocket_input_pump_forwards_messages_and_closes_on_disconnect(monkeypatch):
    ws = object()
    seen: list[str] = []
    responses = iter(["hello", RuntimeError("closed")])

    def fake_recv_message(actual_ws, timeout_seconds=0.2):
        assert actual_ws is ws
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ws_mod, "_recv_message", fake_recv_message)

    pump = ws_mod._WebSocketInputPump(ws, seen.append)
    pump.start()
    pump._thread.join(timeout=1)

    assert seen == ["hello"]
    assert pump.closed is True
