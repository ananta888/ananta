from __future__ import annotations

from agent.services import ssh_terminal_wrapper as stw
from agent.services.ssh_terminal_wrapper import WrapperContext


def test_sanitize_path_rejects_traversal():
    assert stw._sanitize_path("../../etc/passwd") is None


def test_parse_env_context_rejects_invalid_target_type(monkeypatch):
    monkeypatch.setenv("ANANTA_SSH_USER_ID", "u1")
    monkeypatch.setenv("ANANTA_SSH_PRINCIPAL", "ananta-worker-u1")
    monkeypatch.setenv("ANANTA_SSH_TARGET_TYPE", "invalid")
    assert stw._parse_env_context() is None


def test_wrapper_denies_direct_shell_for_managed_target(monkeypatch):
    monkeypatch.setattr(stw.settings, "native_ssh_enabled", True)
    monkeypatch.setattr(stw.settings, "ssh_terminal_wrapper_required", True)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "bash -i")

    wrapper = stw.AnantaSshTerminalWrapper()
    ctx = WrapperContext(
        user_id="u1",
        principal="ananta-worker-u1",
        target_type="worker",
        target_id="alpha",
        workspace_path=None,
        goal_id=None,
        task_id=None,
        operation="create",
        session_id=None,
    )

    code = wrapper.run(ctx=ctx)
    assert code == 1
