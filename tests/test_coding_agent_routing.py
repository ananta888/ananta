from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from agent.cli_backends.sgpt import run_llm_cli_command


def test_profile_backend_runs_through_hub_semaphore_and_headless_policy(tmp_path) -> None:
    permit_calls = []

    @contextmanager
    def permit(backend, *, timeout):
        permit_calls.append((backend, timeout))
        yield SimpleNamespace(acquired=True)

    with (
        patch("agent.cli_backends.sgpt._choose_candidates", return_value=["qwen_code"]),
        patch("agent.cli_backends.sgpt._acquire_backend_permit", side_effect=permit),
        patch(
            "agent.cli_backends.sgpt.run_profile_coding_agent",
            return_value=(0, '{"type":"result"}', ""),
        ) as run_provider,
    ):
        result = run_llm_cli_command(
            "fix tests",
            backend="qwen_code",
            timeout=17,
            workdir=str(tmp_path),
            routing_policy={"coding_agent_permission_mode": "autonomous"},
            session={"id": "session-1"},
        )

    assert result == (0, '{"type":"result"}', "", "qwen_code")
    assert permit_calls == [("qwen_code", 17)]
    run_provider.assert_called_once_with(
        "qwen_code",
        prompt="fix tests",
        model=None,
        timeout=17,
        workdir=str(tmp_path),
        session_id="session-1",
        permission_mode="autonomous",
    )


def test_profile_backend_fails_without_running_when_semaphore_is_exhausted(tmp_path) -> None:
    @contextmanager
    def denied(_backend, *, timeout):
        assert timeout == 9
        yield SimpleNamespace(acquired=False)

    with (
        patch("agent.cli_backends.sgpt._choose_candidates", return_value=["qwen_code"]),
        patch("agent.cli_backends.sgpt._acquire_backend_permit", side_effect=denied),
        patch("agent.cli_backends.sgpt.run_profile_coding_agent") as run_provider,
    ):
        result = run_llm_cli_command(
            "fix tests",
            backend="qwen_code",
            timeout=9,
            workdir=str(tmp_path),
        )

    assert result[0] == -1
    assert result[3] == "qwen_code"
    assert "semaphore_exhausted" in result[2]
    run_provider.assert_not_called()
