from __future__ import annotations

from unittest.mock import patch


class _FakeTerminalSession:
    def __init__(self) -> None:
        self.id = "cli-1"
        self.shell = "/bin/sh"
        self.transport = "pty"
        self.closed = False
        self.created_at = 1.0
        self.updated_at = 1.0
        self._workdir = None
        self.ensure_workdir_calls: list[str] = []
        self.run_command_calls: list[tuple[str, dict]] = []

    def ensure_workdir(self, workdir: str | None) -> None:
        if workdir:
            self.ensure_workdir_calls.append(workdir)
            self._workdir = workdir

    def _ensure_runtime_environment(self, runtime_cfg: dict[str, object]) -> dict[str, str]:
        return {}

    def run_command(self, command: str, **kwargs):
        self.run_command_calls.append((command, kwargs))
        return 0, "ok", ""


def test_ensure_session_for_cli_applies_workdir(app):
    from agent.services.live_terminal_session_service import LiveTerminalSessionService
    from agent.config import settings

    service = LiveTerminalSessionService()
    fake_session = _FakeTerminalSession()

    with (
        patch.object(service, "ensure_session", return_value=fake_session),
        patch.object(settings, "agent_url", "http://worker-test:5000"),
        patch.object(settings, "agent_name", "worker-test"),
    ):
        meta = service.ensure_session_for_cli(
            {"id": "cli-1", "metadata": {"opencode_execution_mode": "interactive_terminal"}},
            execution_mode="interactive_terminal",
            workdir="/tmp/worker-workspace",
        )

    assert fake_session.ensure_workdir_calls == ["/tmp/worker-workspace"]
    assert meta["workdir"] == "/tmp/worker-workspace"
    assert meta["execution_mode"] == "interactive_terminal"
    assert meta["agent_url"] == "http://worker-test:5000"
    assert meta["agent_name"] == "worker-test"


def test_run_opencode_turn_passes_workdir_to_session_setup(app):
    from agent.services.live_terminal_session_service import LiveTerminalSessionService

    service = LiveTerminalSessionService()
    fake_session = _FakeTerminalSession()

    with (
        patch("agent.services.live_terminal_session_service.shutil.which", return_value="/usr/bin/opencode"),
        patch("agent.services.live_terminal_session_service.LiveTerminalSessionService.ensure_session_for_cli") as ensure_for_cli,
        patch.object(service, "ensure_session", return_value=fake_session),
    ):
        ensure_for_cli.return_value = {
            "terminal_session_id": "cli-1",
            "execution_mode": "interactive_terminal",
            "workdir": "/tmp/task-workspace",
        }
        rc, out, err = service.run_opencode_turn(
            {"id": "cli-1", "metadata": {}},
            prompt="say hi",
            workdir="/tmp/task-workspace",
        )

    assert rc == 0
    assert out == "ok"
    assert err == ""
    assert ensure_for_cli.call_args.kwargs["workdir"] == "/tmp/task-workspace"
    assert fake_session.run_command_calls
    command, kwargs = fake_session.run_command_calls[0]
    assert "--dir /tmp/task-workspace" in command
    assert kwargs["visible_command"] == command
    assert kwargs["suppress_input_echo"] is True
