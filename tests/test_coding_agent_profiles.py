from pathlib import Path

import pytest

from agent.cli_backends.coding_agent_contract import CodingAgentRunRequest, ProcessExecutionResult
from agent.cli_backends.coding_agent_profiles import CLI_PROFILES, CliCodingAgentProvider


class RecordingRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessExecutionResult(0, '{"type":"result"}\n', "", "completed", 7)


@pytest.mark.parametrize("provider_id", ["qwen_code", "gemini_cli", "copilot_cli", "cline", "kilo_code"])
def test_profile_provider_is_headless_and_shell_free(provider_id: str, tmp_path: Path) -> None:
    runner = RecordingRunner()
    provider = CliCodingAgentProvider(
        CLI_PROFILES[provider_id],
        process_runner=runner,
        binary_resolver=lambda _name: f"/opt/bin/{provider_id}",
        version_probe=lambda _binary, _arguments: (0, "0.22.2" if provider_id == "qwen_code" else "1.2.3"),
        environment={"PATH": "/opt/bin", "UNRELATED_SECRET": "must-not-leak"},
    )
    result = provider.run(CodingAgentRunRequest(prompt="implement task", workspace=tmp_path, timeout_seconds=30))

    assert result.succeeded
    argv, kwargs = runner.calls[0]
    assert argv[0].startswith("/opt/bin/")
    assert "implement task" in argv or kwargs["input_text"] == "implement task\n"
    assert "UNRELATED_SECRET" not in kwargs["environment"]
    assert kwargs["event_sink"] is None


def test_qwen_profile_has_structured_bounded_auto_edit_mode(tmp_path: Path) -> None:
    argv, input_text = CLI_PROFILES["qwen_code"].command(
        "/opt/bin/qwen",
        CodingAgentRunRequest(
            prompt="fix tests",
            workspace=tmp_path,
            timeout_seconds=600,
            model="qwen3-coder-plus",
            session_id="session-1",
        ),
    )

    assert input_text is None
    assert argv[-1] == "fix tests"
    assert ("--output-format", "stream-json") == argv[1:3]
    assert "--safe-mode" in argv
    assert argv[argv.index("--approval-mode") + 1] == "auto-edit"
    assert argv[argv.index("--max-tool-calls") + 1] == "50"
    assert argv[argv.index("--resume") + 1] == "session-1"
    assert argv[argv.index("--max-wall-time") + 1] == "600s"


def test_qwen_probe_rejects_unverified_version_instead_of_silent_execution() -> None:
    provider = CliCodingAgentProvider(
        CLI_PROFILES["qwen_code"],
        binary_resolver=lambda _name: "/opt/bin/qwen",
        version_probe=lambda _binary, _arguments: (0, "qwen 1.0.0"),
        environment={},
    )

    probe = provider.detect()

    assert probe.state.value == "error"
    assert probe.reason_code == "version_unverified"


def test_qwen_current_auth_and_model_environment_are_classified_separately(tmp_path: Path) -> None:
    runner = RecordingRunner()
    provider = CliCodingAgentProvider(
        CLI_PROFILES["qwen_code"],
        process_runner=runner,
        binary_resolver=lambda _name: "/opt/bin/qwen",
        version_probe=lambda _binary, _arguments: (0, "0.22.2"),
        environment={
            "PATH": "/opt/bin",
            "BAILIAN_CODING_PLAN_API_KEY": "current-secret",
            "OPENAI_BASE_URL": "http://127.0.0.1:9000/v1",
            "QWEN_MODEL": "qwen3-coder",
        },
    )

    assert provider.detect().auth_status.value == "ready"
    provider.run(CodingAgentRunRequest(prompt="fix", workspace=tmp_path, timeout_seconds=60))

    _argv, kwargs = runner.calls[0]
    assert kwargs["environment"]["BAILIAN_CODING_PLAN_API_KEY"] == "current-secret"
    assert kwargs["environment"]["OPENAI_BASE_URL"] == "http://127.0.0.1:9000/v1"
    assert kwargs["environment"]["QWEN_MODEL"] == "qwen3-coder"
    assert kwargs["secret_values"] == ("current-secret",)


def test_qwen_endpoint_without_credential_does_not_claim_auth_ready() -> None:
    provider = CliCodingAgentProvider(
        CLI_PROFILES["qwen_code"],
        binary_resolver=lambda _name: "/opt/bin/qwen",
        version_probe=lambda _binary, _arguments: (0, "0.22.2"),
        environment={"OPENAI_BASE_URL": "http://127.0.0.1:9000/v1", "QWEN_MODEL": "qwen3-coder"},
    )

    assert provider.detect().auth_status.value == "unknown"


def test_provider_normalizes_quota_failure_for_fallback(tmp_path: Path) -> None:
    class QuotaRunner:
        def run(self, _argv, **_kwargs):
            return ProcessExecutionResult(1, "", "API quota exceeded", "process_failed", 5)

    provider = CliCodingAgentProvider(
        CLI_PROFILES["qwen_code"],
        process_runner=QuotaRunner(),
        binary_resolver=lambda _name: "/opt/bin/qwen",
        version_probe=lambda _binary, _arguments: (0, "0.22.2"),
        environment={},
    )

    result = provider.run(CodingAgentRunRequest(prompt="fix", workspace=tmp_path, timeout_seconds=60))

    assert result.reason_code == "quota_exhausted"


def test_cline_profile_auto_approves_without_human_and_bounds_commands(tmp_path: Path) -> None:
    runner = RecordingRunner()
    provider = CliCodingAgentProvider(
        CLI_PROFILES["cline"],
        process_runner=runner,
        binary_resolver=lambda _name: "/opt/bin/cline",
        version_probe=lambda _binary, _arguments: (0, "1.0"),
        environment={"PATH": "/opt/bin"},
    )
    provider.run(CodingAgentRunRequest(prompt="fix", workspace=tmp_path, timeout_seconds=60))

    argv, kwargs = runner.calls[0]
    assert argv[argv.index("--auto-approve") + 1] == "true"
    assert argv[argv.index("--timeout") + 1] == "60"
    assert "sudo *" in kwargs["environment"]["CLINE_COMMAND_PERMISSIONS"]
