"""Execution-only Pi provider: explicit authorization, private cwd, no tools."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.cli_backends.coding_agent_contract import CodingAgentRunRequest, ProcessExecutionResult, ProviderState
from agent.cli_backends.coding_agent_profiles import build_cli_coding_agent_provider, coding_agent_descriptors
from agent.cli_backends.coding_agent_targets import CodingAgentInferenceTarget
from agent.cli_backends.pi_provider import PiCodingAgentProvider
from tests.pi_protocol_examples import pi_events


def target(**overrides):
    value = CodingAgentInferenceTarget(
        client_id="pi", provider_id="ollama", model="selected-model", cli_model="selected-model",
        base_url="http://model-worker:11434/v1", target_kind="local_openai", api_key="synthetic-private-key",
    )
    return replace(value, **overrides)


def runtime():
    return {
        "installed": True, "status": "ready", "version": "0.85.1", "binary_path": "/pinned/pi",
        "version_probe": {"rc": 0, "stdout": "0.85.1", "stderr": ""},
    }


class Runner:
    def __init__(self, *, return_code=0, reason="completed", truncate=False, malformed=False,
                 key="synthetic-private-key", answer="A\u2028B"):
        self.calls = []
        self.return_code, self.reason, self.truncate, self.malformed = return_code, reason, truncate, malformed
        self.key, self.answer = key, answer

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        config = Path(kwargs["environment"]["PI_CODING_AGENT_DIR"])
        assert argv[:3] == ("/pinned/node", "/pinned/pi_sdk_entry.mjs", "/pinned/sdk.js")
        assert argv[3:] == (str(config),)
        assert kwargs["input_text"] == "Explain this code.\n"
        assert kwargs["secret_values"] == (self.key,)
        assert "event_sink" not in kwargs
        for path in config.iterdir():
            assert path.stat().st_mode & 0o777 == 0o400
            assert self.key not in path.read_text()
        models = json.loads((config / "models.json").read_text())
        assert list(models["providers"]) == ["ananta"]
        assert models["providers"]["ananta"]["apiKey"] == "$ANANTA_PI_API_KEY"
        assert models["providers"]["ananta"]["authHeader"] is True
        settings = json.loads((config / "settings.json").read_text())
        assert settings["retry"]["enabled"] is False and settings["compaction"]["enabled"] is False
        assert settings["defaultTools"] == []
        records = pi_events(workspace=str(kwargs["cwd"]), answer=self.answer)
        stdout = "broken" if self.malformed else "\n".join(json.dumps(event) for event in records)
        return ProcessExecutionResult(self.return_code, stdout, "", self.reason, 1, self.truncate)


def request(tmp_path, **overrides):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return replace(CodingAgentRunRequest(
        prompt="Explain this code.", workspace=project, timeout_seconds=10, permission_mode="read_only",
    ), **overrides)


def provider(tmp_path, runner, **overrides):
    options = dict(
        enabled=True, target=target(), authorize=lambda _: True, process_runner=runner,
        runtime_probe=runtime, runtime_root=tmp_path,
        command_builder=lambda _: ("/pinned/node", "/pinned/pi_sdk_entry.mjs", "/pinned/sdk.js"),
        environment={"PATH": "/usr/bin", "AGENT_TOKEN": "foreign", "HOME": "/foreign", "NODE_OPTIONS": "foreign"},
    )
    options.update(overrides)
    return PiCodingAgentProvider(**options)


def test_pi_authorized_no_tools_run_never_uses_project_cwd_or_foreign_environment(tmp_path):
    runner, sink = Runner(), Mock()
    req = request(tmp_path)
    legacy = req.workspace / ".pi" / "commands"
    legacy.mkdir(parents=True)
    result = provider(tmp_path, runner).run(req, event_sink=sink)
    assert result.succeeded and result.stdout == "A\u2028B"
    assert legacy.is_dir() and not (legacy.parent / "prompts").exists()
    _, call = runner.calls[0]
    assert call["cwd"] != req.workspace and req.workspace not in call["cwd"].parents
    assert not call["cwd"].parent.exists()
    assert {"HOME", "AGENT_TOKEN", "NODE_OPTIONS"}.isdisjoint(call["environment"])
    assert call["environment"]["ANANTA_PI_API_KEY"] == "synthetic-private-key"
    assert sink.call_count == 1 and sink.call_args.args[0].text == result.stdout


@pytest.mark.parametrize("options,reason", [
    ({"enabled": False}, "pi_disabled"), ({"authorize": None}, "pi_execution_not_authorized"),
    ({"target": target(api_key=None)}, "pi_auth_required"),
    ({"target": target(provider_id="unselected")}, "pi_target_unsupported"),
    ({"target": target(base_url="http://user:password@host/v1")}, "pi_endpoint_invalid"),
])
def test_pi_unready_or_unauthorized_never_executes(tmp_path, options, reason):
    runner = Runner()
    result = provider(tmp_path, runner, **options).run(request(tmp_path))
    assert not result.succeeded and result.reason_code == reason and runner.calls == []


@pytest.mark.parametrize("options", [
    {"session_id": "foreign-session"}, {"permission_mode": "workspace_write"},
    {"permission_mode": "autonomous"}, {"model": "different-model"},
])
def test_pi_rejects_unsupported_capability_or_model_before_process(tmp_path, options):
    runner = Runner()
    result = provider(tmp_path, runner).run(request(tmp_path, **options))
    assert not result.succeeded and runner.calls == []


@pytest.mark.parametrize("options", [
    {"return_code": 124, "reason": "timeout"}, {"return_code": 130, "reason": "cancelled"},
    {"return_code": 65, "reason": "output_limit_exceeded", "truncate": True}, {"malformed": True},
])
def test_pi_failure_never_publishes_partial_text_and_cleans_runtime(tmp_path, options):
    runner, sink = Runner(**options), Mock()
    result = provider(tmp_path, runner).run(request(tmp_path), event_sink=sink)
    assert not result.succeeded and result.stdout == "" and not sink.called
    assert not runner.calls[0][1]["cwd"].parent.exists()


def test_pi_authorization_revoked_during_execution_discards_result(tmp_path):
    authorize, runner = Mock(side_effect=[True, True, False]), Runner()
    result = provider(tmp_path, runner, authorize=authorize).run(request(tmp_path))
    assert result.reason_code == "pi_execution_not_authorized" and result.stdout == ""
    assert authorize.call_count == 3


def test_pi_runtime_cannot_be_placed_inside_user_project(tmp_path):
    runner, req = Runner(), request(tmp_path)
    result = provider(tmp_path, runner, runtime_root=req.workspace).run(req)
    assert not result.succeeded and runner.calls == []
    assert list(req.workspace.iterdir()) == []


def test_pi_remains_disabled_by_default_and_has_no_unverified_capabilities():
    value = build_cli_coding_agent_provider("pi")
    assert value.detect().state is ProviderState.UNSUPPORTED
    assert not value.descriptor.enabled_by_default
    assert not any((value.descriptor.capabilities.tools, value.descriptor.capabilities.sandbox,
                    value.descriptor.capabilities.session_resume, value.descriptor.capabilities.git_changes))
    assert value.descriptor in coding_agent_descriptors()


def test_pi_revalidates_authority_after_runtime_probe_before_model_call(tmp_path):
    runner = Runner()
    value = provider(tmp_path, runner, authorize=Mock(side_effect=[True, False]))
    result = value.run(request(tmp_path))
    assert result.reason_code == "pi_execution_not_authorized" and runner.calls == []


@pytest.mark.parametrize("key", ["synthetic-key", 'quoted"key', "back\\slash", "abc"])
def test_pi_redacts_credentials_after_json_decoding(tmp_path, key):
    runner, sink = Runner(key=key, answer=f"echo {key}"), Mock()
    result = provider(tmp_path, runner, target=target(api_key=key)).run(request(tmp_path), event_sink=sink)
    assert result.succeeded and result.stdout == "echo <redacted>"
    assert sink.call_args.args[0].text == "echo <redacted>"
