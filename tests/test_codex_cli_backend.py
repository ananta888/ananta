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
