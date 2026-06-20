import time
from unittest.mock import MagicMock, patch



# Split from tests/test_codex_cli_backend.py to keep source files below 1000 lines.

def test_resolve_codex_runtime_config_prefers_runtime_app_state_over_settings_defaults(app):
    from agent.common.sgpt import resolve_codex_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {},
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://10.0.0.5:1234/v1/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "codex": "https://api.openai.com/v1/chat/completions",
        }
        with patch("agent.cli_backends.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://wrong.example/v1/chat/completions"
            mock_settings.openai_api_key = None

            resolved = resolve_codex_runtime_config()

    assert resolved["base_url"] == "http://10.0.0.5:1234/v1"
    assert resolved["base_url_source"] == "lmstudio_url"
    assert resolved["is_local"] is True


def test_resolve_codex_runtime_config_supports_custom_local_openai_target(app):
    from agent.common.sgpt import resolve_codex_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "codex_cli": {"target_provider": "vllm_local"},
            "local_openai_backends": [
                {
                    "id": "vllm_local",
                    "base_url": "http://127.0.0.1:8010/v1/chat/completions",
                    "api_key_profile": "local-dev",
                }
            ],
            "llm_api_key_profiles": {"local-dev": {"api_key": "sk-local-vllm"}},
        }
        app.config["PROVIDER_URLS"] = {}
        with patch("agent.cli_backends.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = ""
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None

            resolved = resolve_codex_runtime_config()

    assert resolved["base_url"] == "http://127.0.0.1:8010/v1"
    assert resolved["target_provider"] == "vllm_local"
    assert resolved["base_url_source"] == "codex_cli.target_provider:vllm_local"
    assert resolved["api_key"] == "sk-local-vllm"
    assert resolved["target_kind"] == "local_openai"


def test_resolve_codex_runtime_config_marks_remote_ananta_target_kind(app):
    from agent.common.sgpt import resolve_codex_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "codex_cli": {"target_provider": "ananta_remote_prod"},
            "remote_ananta_backends": [
                {
                    "id": "ananta_remote_prod",
                    "base_url": "https://remote-ananta.example/v1/chat/completions",
                    "instance_id": "hub-remote-1",
                    "max_hops": 6,
                }
            ],
        }
        app.config["PROVIDER_URLS"] = {}
        with patch("agent.cli_backends.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = ""
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = "sk-cloud"

            resolved = resolve_codex_runtime_config()

    assert resolved["target_provider"] == "ananta_remote_prod"
    assert resolved["target_kind"] == "remote_ananta_hub"
    assert resolved["remote_hub"] is True
    assert resolved["instance_id"] == "hub-remote-1"
    assert resolved["max_hops"] == 6


def test_run_codex_command_fails_closed_when_runtime_target_missing(app):
    from agent.common.sgpt import run_codex_command

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"default_provider": "openai", "codex_cli": {"prefer_lmstudio": False}}
        app.config["PROVIDER_URLS"] = {}
        with (
            patch("agent.cli_backends.sgpt.shutil.which", return_value=r"C:\tools\codex.cmd"),
            patch("agent.cli_backends.sgpt.settings") as mock_settings,
            patch("agent.cli_backends.sgpt.subprocess.run") as mock_run,
        ):
            mock_settings.codex_path = "codex"
            mock_settings.codex_default_model = "gpt-5-codex"
            mock_settings.default_provider = "openai"
            mock_settings.openai_url = ""
            mock_settings.openai_api_key = None
            mock_settings.lmstudio_url = ""

            rc, out, err = run_codex_command("generate fix")

    assert rc == -1
    assert out == ""
    assert "missing OpenAI-compatible base_url" in err
    mock_run.assert_not_called()


def test_run_llm_cli_command_falls_back_from_codex_to_opencode_for_degraded_auto_mode():
    from agent.common import sgpt as sgpt_mod

    runtime_before = {name: dict(values) for name, values in sgpt_mod._BACKEND_RUNTIME.items()}
    try:
        for values in sgpt_mod._BACKEND_RUNTIME.values():
            values.update(
                {
                    "last_success_at": None,
                    "last_failure_at": None,
                    "consecutive_failures": 0,
                    "cooldown_until": 0.0,
                    "total_success": 0,
                    "total_failures": 0,
                    "last_error": "",
                    "last_rc": None,
                    "last_latency_ms": None,
                }
            )

        with (
            patch("agent.cli_backends.sgpt.settings") as mock_settings,
            patch("agent.cli_backends.sgpt.run_codex_command", return_value=(-1, "", "codex unavailable")),
            patch("agent.cli_backends.sgpt.run_opencode_command", return_value=(0, "ok via opencode", "")),
        ):
            mock_settings.sgpt_execution_backend = "codex"

            rc, out, err, backend = sgpt_mod.run_llm_cli_command(
                prompt="fix failing test",
                backend="auto",
                routing_policy={"allowed_backends": ["codex", "opencode"]},
            )

        assert rc == 0
        assert out == "ok via opencode"
        assert err == ""
        assert backend == "opencode"
        assert sgpt_mod._BACKEND_RUNTIME["codex"]["consecutive_failures"] == 1
        assert sgpt_mod._BACKEND_RUNTIME["opencode"]["total_success"] == 1
    finally:
        for name, values in runtime_before.items():
            sgpt_mod._BACKEND_RUNTIME[name].clear()
            sgpt_mod._BACKEND_RUNTIME[name].update(values)


def test_run_llm_cli_command_skips_cooldown_backend_when_alternative_is_available():
    from agent.common import sgpt as sgpt_mod

    runtime_before = {name: dict(values) for name, values in sgpt_mod._BACKEND_RUNTIME.items()}
    try:
        for values in sgpt_mod._BACKEND_RUNTIME.values():
            values.update(
                {
                    "last_success_at": None,
                    "last_failure_at": None,
                    "consecutive_failures": 0,
                    "cooldown_until": 0.0,
                    "total_success": 0,
                    "total_failures": 0,
                    "last_error": "",
                    "last_rc": None,
                    "last_latency_ms": None,
                }
            )
        sgpt_mod._BACKEND_RUNTIME["codex"]["cooldown_until"] = time.time() + 30

        with (
            patch("agent.cli_backends.sgpt.settings") as mock_settings,
            patch("agent.cli_backends.sgpt.run_codex_command") as mock_codex,
            patch("agent.cli_backends.sgpt.run_opencode_command", return_value=(0, "ok after cooldown skip", "")),
        ):
            mock_settings.sgpt_execution_backend = "codex"

            rc, out, err, backend = sgpt_mod.run_llm_cli_command(
                prompt="fix broken deployment",
                backend="auto",
                routing_policy={"allowed_backends": ["codex", "opencode"]},
            )

        assert rc == 0
        assert out == "ok after cooldown skip"
        assert err == ""
        assert backend == "opencode"
        mock_codex.assert_not_called()
    finally:
        for name, values in runtime_before.items():
            sgpt_mod._BACKEND_RUNTIME[name].clear()
            sgpt_mod._BACKEND_RUNTIME[name].update(values)


def test_run_llm_cli_command_prefixes_openai_provider_for_opencode_model():
    from agent.common import sgpt as sgpt_mod

    with (
        patch("agent.cli_backends.sgpt.settings") as mock_settings,
        patch("agent.cli_backends.sgpt.run_opencode_command", return_value=(0, "ok", "")) as mock_run_opencode,
    ):
        mock_settings.default_provider = "openai"
        rc, out, err, backend = sgpt_mod.run_llm_cli_command(
            prompt="create plan",
            backend="opencode",
            model="gpt-4o-mini",
        )

    assert rc == 0
    assert out == "ok"
    assert err == ""
    assert backend == "opencode"
    assert mock_run_opencode.call_args.kwargs["model"] == "openai/gpt-4o-mini"
