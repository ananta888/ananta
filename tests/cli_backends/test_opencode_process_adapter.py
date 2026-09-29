from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from agent.cli_backends.coding_agent_contract import CodingAgentRunResult, ProcessExecutionResult
from agent.cli_backends.coding_agent_targets import CodingAgentInferenceTarget
from agent.cli_backends.opencode import _run_opencode_subprocess, run_aider_command


class RecordingProcessRunner:
    def __init__(self, result: ProcessExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return self.result


@contextmanager
def _permit(*_args, **_kwargs):
    yield SimpleNamespace(acquired=True)


def test_opencode_uses_common_bounded_process_contract_and_environment_allowlist(tmp_path) -> None:
    cancellation = Event()
    events = []
    runner = RecordingProcessRunner(ProcessExecutionResult(0, "ok", "", "completed", 3))

    with (
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch(
            "agent.cli_backends.opencode.resolve_opencode_runtime_config",
            return_value={"model": "openai/test", "provider_config": None, "diagnostics": []},
        ),
        patch.dict(
            "agent.cli_backends.opencode.os.environ",
            {"PATH": "/opt/bin", "OPENAI_API_KEY": "secret-token", "UNRELATED_SECRET": "must-not-leak"},
            clear=True,
        ),
    ):
        result = _run_opencode_subprocess(
            prompt="fix tests",
            model=None,
            timeout=30,
            workdir=str(tmp_path),
            output_format="json",
            cancellation=cancellation,
            event_sink=events.append,
            maximum_output_chars=8192,
            process_runner=runner,
        )

    assert result[:3] == (0, "ok", "")
    argv, kwargs = runner.calls[0]
    assert argv == ("/opt/bin/opencode", "run", "--model", "openai/test", "--format", "json")
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["input_text"] == "fix tests"
    assert kwargs["cancellation"] is cancellation
    assert kwargs["event_sink"] == events.append
    assert kwargs["maximum_output_chars"] == 8192
    assert kwargs["environment"]["OPENAI_API_KEY"] == "secret-token"
    assert "UNRELATED_SECRET" not in kwargs["environment"]
    assert "secret-token" in kwargs["secret_values"]


def test_opencode_preserves_public_timeout_tuple_from_common_adapter(tmp_path) -> None:
    runner = RecordingProcessRunner(ProcessExecutionResult(124, "partial", "", "timeout", 20))

    with (
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch(
            "agent.cli_backends.opencode.resolve_opencode_runtime_config",
            return_value={"model": None, "provider_config": None, "diagnostics": []},
        ),
    ):
        result = _run_opencode_subprocess(
            prompt="fix tests",
            model=None,
            timeout=1,
            workdir=str(tmp_path),
            output_format=None,
            process_runner=runner,
        )

    assert result[:3] == (-1, "", "Timeout")


def test_opencode_maps_automatic_cancellation_without_human_confirmation(tmp_path) -> None:
    runner = RecordingProcessRunner(ProcessExecutionResult(130, "partial", "", "cancelled", 5))

    with (
        patch("agent.cli_backends.opencode.shutil.which", return_value="/opt/bin/opencode"),
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch(
            "agent.cli_backends.opencode.resolve_opencode_runtime_config",
            return_value={"model": None, "provider_config": None, "diagnostics": []},
        ),
    ):
        result = _run_opencode_subprocess(
            prompt="fix tests",
            model=None,
            timeout=1,
            workdir=str(tmp_path),
            output_format=None,
            cancellation=Event(),
            process_runner=runner,
        )

    assert result[:3] == (130, "partial", "Cancelled")


def test_aider_uses_profile_provider_with_separate_inference_target(tmp_path) -> None:
    captured = {}
    target = CodingAgentInferenceTarget(
        client_id="aider",
        provider_id="local_coder",
        model="qwen3-coder",
        cli_model="openai/qwen3-coder",
        base_url="http://127.0.0.1:9000/v1",
        target_kind="local_openai",
        api_key="sk-no-key-needed",
        api_key_source="local_dummy",
    )

    class Provider:
        def run(self, request, *, event_sink=None):
            captured["request"] = request
            captured["event_sink"] = event_sink
            return CodingAgentRunResult("aider", 0, "ok", "", "completed", 4)

    def build_provider(provider_id, **kwargs):
        captured["provider_id"] = provider_id
        captured["environment"] = kwargs["environment"]
        return Provider()

    with (
        patch("agent.cli_backends.opencode._acquire_backend_permit", _permit),
        patch("agent.cli_backends.opencode.resolve_aider_inference_target", return_value=target),
        patch("agent.cli_backends.opencode.build_cli_coding_agent_provider", side_effect=build_provider),
    ):
        result = run_aider_command("fix tests", timeout=30, workdir=str(tmp_path))

    assert result == (0, "ok", "")
    assert captured["provider_id"] == "aider"
    assert captured["request"].workspace == tmp_path.resolve()
    assert captured["request"].model == "openai/qwen3-coder"
    assert captured["environment"]["OPENAI_BASE_URL"] == "http://127.0.0.1:9000/v1"
    assert captured["environment"]["OPENAI_API_KEY"] == "sk-no-key-needed"
