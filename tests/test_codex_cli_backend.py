from unittest.mock import MagicMock, patch


def test_run_codex_command_injects_lmstudio_openai_compatible_env():
    from agent.common.sgpt import run_codex_command

    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\codex.cmd"),
        patch("agent.common.sgpt.settings") as mock_settings,
        patch("agent.common.sgpt.subprocess.run") as mock_run,
    ):
        mock_settings.codex_path = "codex"
        mock_settings.codex_default_model = "gpt-5-codex"
        mock_settings.default_provider = "lmstudio"
        mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
        mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.openai_api_key = None

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        rc, out, err = run_codex_command("analyze repository")

    assert rc == 0
    assert out == "ok"
    assert err == ""
    args = mock_run.call_args[0][0]
    assert args[:3] == [r"C:\tools\codex.cmd", "exec", "--skip-git-repo-check"]
    env = mock_run.call_args[1]["env"]
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:1234/v1"
    assert env["OPENAI_API_BASE"] == "http://127.0.0.1:1234/v1"
    assert env["OPENAI_API_KEY"] == "sk-no-key-needed"


def test_run_codex_command_prefers_explicit_codex_cli_runtime_from_agent_config(app):
    from agent.common.sgpt import run_codex_command

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "codex_cli": {
                "base_url": "http://192.168.1.10:1234/v1/chat/completions",
                "api_key_profile": "codex-local",
                "prefer_lmstudio": False,
            },
            "llm_api_key_profiles": {
                "codex-local": {"provider": "codex", "api_key": "sk-local-profile"},
            },
        }
        with (
            patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\codex.cmd"),
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run") as mock_run,
        ):
            mock_settings.codex_path = "codex"
            mock_settings.codex_default_model = "gpt-5-codex"
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = "sk-cloud"

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ok"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            rc, out, err = run_codex_command("review repository")

    assert rc == 0
    assert out == "ok"
    assert err == ""
    env = mock_run.call_args[1]["env"]
    assert env["OPENAI_BASE_URL"] == "http://192.168.1.10:1234/v1"
    assert env["OPENAI_API_BASE"] == "http://192.168.1.10:1234/v1"
    assert env["OPENAI_API_KEY"] == "sk-local-profile"


def test_resolve_codex_runtime_config_exposes_source_metadata_for_local_runtime(app):
    from agent.common.sgpt import resolve_codex_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "codex_cli": {
                "base_url": "http://127.0.0.1:1234/v1/chat/completions",
                "prefer_lmstudio": True,
            },
        }
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None

            resolved = resolve_codex_runtime_config()

    assert resolved["base_url"] == "http://127.0.0.1:1234/v1"
    assert resolved["base_url_source"] == "codex_cli.base_url"
    assert resolved["api_key"] == "sk-no-key-needed"
    assert resolved["api_key_source"] == "local_dummy"
    assert resolved["is_local"] is True


def test_resolve_codex_runtime_config_falls_back_to_openai_when_lmstudio_not_preferred():
    from agent.common.sgpt import resolve_codex_runtime_config

    with patch("agent.common.sgpt.settings") as mock_settings:
        mock_settings.default_provider = "openai"
        mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
        mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
        mock_settings.openai_api_key = "sk-cloud"

        resolved = resolve_codex_runtime_config()

    assert resolved["base_url"] == "https://api.openai.com/v1"
    assert resolved["base_url_source"] == "default_provider"
    assert resolved["api_key"] == "sk-cloud"
    assert resolved["api_key_source"] == "openai_api_key"
    assert resolved["is_local"] is False


def test_get_cli_backend_preflight_reports_cli_and_provider_diagnostics(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {"prefer_lmstudio": True},
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://192.168.1.25:1234/v1/chat/completions",
        }
        with (
            patch("agent.common.sgpt.shutil.which", side_effect=lambda cmd: f"/mock/{cmd}" if cmd == "codex" else None),
            patch("agent.llm_integration.probe_lmstudio_runtime", return_value={
                "ok": True,
                "status": "ok",
                "models_url": "http://192.168.1.25:1234/v1/models",
                "candidate_count": 4,
                "candidates": [{"id": "qwen2.5-coder"}],
            }),
            patch("agent.common.sgpt.settings") as mock_settings,
        ):
            mock_settings.codex_path = "codex"
            mock_settings.opencode_path = "opencode"
            mock_settings.aider_path = "aider"
            mock_settings.mistral_code_path = "mistral-code"
            mock_settings.default_provider = "lmstudio"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None
            mock_settings.http_timeout = 5.0

            preflight = get_cli_backend_preflight()

    assert preflight["cli_backends"]["codex"]["binary_available"] is True
    assert preflight["cli_backends"]["codex"]["binary_path"] == "/mock/codex"
    assert preflight["cli_backends"]["opencode"]["binary_available"] is False
    assert preflight["providers"]["lmstudio"]["base_url"] == "http://192.168.1.25:1234/v1"
    assert preflight["providers"]["lmstudio"]["host_kind"] == "private_network"
    assert preflight["providers"]["lmstudio"]["candidate_count"] == 4
    assert preflight["providers"]["codex"]["base_url"] == "http://192.168.1.25:1234/v1"
    assert preflight["providers"]["codex"]["base_url_source"] == "lmstudio_url"
    assert preflight["providers"]["codex"]["api_key_source"] == "local_dummy"


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
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://wrong.example/v1/chat/completions"
            mock_settings.openai_api_key = None

            resolved = resolve_codex_runtime_config()

    assert resolved["base_url"] == "http://10.0.0.5:1234/v1"
    assert resolved["base_url_source"] == "lmstudio_url"
    assert resolved["is_local"] is True
