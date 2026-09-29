from __future__ import annotations

from agent.services import ssh_terminal_wrapper as stw
from agent.services.ssh_terminal_wrapper import WrapperContext


def test_sanitize_path_rejects_traversal():
    assert stw._sanitize_path("../../etc/passwd") is None


def test_sanitize_path_rejects_blocked_fragments(monkeypatch):
    monkeypatch.setenv("ANANTA_WORKSPACE_ROOTS", "/workspace,/project-workspaces")
    monkeypatch.setenv("ANANTA_BLOCKED_PATH_FRAGMENTS", "/.ssh,/etc/")
    assert stw._sanitize_path("/workspace/.ssh/id_rsa") is None


def test_sanitize_path_allows_multiple_workspace_roots(monkeypatch):
    monkeypatch.setenv("ANANTA_WORKSPACE_ROOTS", "/workspace,/project-workspaces")
    sanitized = stw._sanitize_path("/project-workspaces/goal-1")
    assert sanitized == "/project-workspaces/goal-1"


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


def test_wrapper_worker_principal_cannot_access_hub():
    wrapper = stw.AnantaSshTerminalWrapper()
    ctx = WrapperContext(
        user_id="u1",
        principal="ananta-worker-u1",
        target_type="hub",
        target_id="hub",
        workspace_path=None,
        goal_id=None,
        task_id=None,
        operation="create",
        session_id=None,
    )
    decision = wrapper._evaluate_policy(ctx, wrapper._build_user_ctx_from_principal(ctx))
    assert decision.allowed is False
    assert decision.reason_code == "ssh_wrapper_worker_principal_cannot_access_hub"
