import time
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


def test_run_sgpt_command_prefers_runtime_openai_provider_config(app):
    from agent.common.sgpt import run_sgpt_command

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "openai",
            "sgpt_default_model": "gpt-4o-mini",
        }
        app.config["PROVIDER_URLS"] = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "lmstudio": "http://127.0.0.1:1234/v1",
        }
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run") as mock_run,
            patch.dict("agent.common.sgpt.os.environ", {}, clear=True),
        ):
            mock_settings.sgpt_default_model = "ananta-default"
            mock_settings.default_provider = "lmstudio"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = "sk-cloud"

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ok"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            rc, out, err = run_sgpt_command("say hi")

    assert rc == 0
    assert out == "ok"
    assert err == ""
    args = mock_run.call_args[0][0]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "gpt-4o-mini"
    env = mock_run.call_args[1]["env"]
    assert env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert env["OPENAI_API_BASE"] == "https://api.openai.com/v1"
    assert env["OPENAI_API_KEY"] == "sk-cloud"


def test_run_sgpt_command_uses_lmstudio_runtime_base_url(app):
    from agent.common.sgpt import run_sgpt_command

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "sgpt_default_model": "nvidia/nemotron-3-nano-4b",
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://192.168.1.10:1234/v1/chat/completions",
        }
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run") as mock_run,
            patch.dict("agent.common.sgpt.os.environ", {}, clear=True),
        ):
            mock_settings.sgpt_default_model = "ananta-default"
            mock_settings.default_provider = "openai"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "ok"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            rc, out, err = run_sgpt_command("say hi")

    assert rc == 0
    assert out == "ok"
    assert err == ""
    args = mock_run.call_args[0][0]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "nvidia/nemotron-3-nano-4b"
    env = mock_run.call_args[1]["env"]
    assert env["OPENAI_BASE_URL"] == "http://192.168.1.10:1234/v1"
    assert env["OPENAI_API_BASE"] == "http://192.168.1.10:1234/v1"
    assert env["OPENAI_API_KEY"] == "sk-no-key-needed"


def test_resolve_opencode_runtime_config_builds_ollama_openai_compatible_provider(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "opencode_default_model": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
        }
        app.config["PROVIDER_URLS"] = {
            "ollama": "http://127.0.0.1:11434/api/generate",
        }
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "ollama"
            mock_settings.opencode_default_model = "opencode/glm-5-free"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/generate"
            mock_settings.http_timeout = 30

            resolved = resolve_opencode_runtime_config()

    assert resolved["model"] == "ollama/lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest"
    assert resolved["base_url"] == "http://127.0.0.1:11434/v1"
    assert resolved["target_provider"] == "ollama"
    provider_cfg = resolved["provider_config"]
    assert provider_cfg["provider"]["ollama"]["npm"] == "@ai-sdk/openai-compatible"
    assert provider_cfg["provider"]["ollama"]["models"] == {"lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest": {}}
    assert provider_cfg["provider"]["ollama"]["options"]["baseURL"] == "http://127.0.0.1:11434/v1"


def test_resolve_opencode_runtime_config_prefixes_hosted_openai_model(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "openai",
            "default_model": "gpt-4o-mini",
            "opencode_default_model": "gpt-4o-mini",
            "opencode_runtime": {"tool_mode": "full", "execution_mode": "live_terminal", "target_provider": None},
        }
        app.config["PROVIDER_URLS"] = {"openai": "https://api.openai.com/v1/chat/completions"}
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "openai"
            mock_settings.default_model = "gpt-4o-mini"
            mock_settings.opencode_default_model = "gpt-4o-mini"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.http_timeout = 30

            resolved = resolve_opencode_runtime_config()

    assert resolved["target_provider"] == "openai"
    assert resolved["target_model"] == "gpt-4o-mini"
    assert resolved["model"] == "openai/gpt-4o-mini"
    assert resolved["base_url"] is None
    assert resolved["provider_config"] is None


def test_run_opencode_command_writes_temp_provider_config_for_ollama(app):
    from agent.common.sgpt import run_opencode_command

    captured = {}

    def _fake_run(args, **kwargs):
        env = kwargs["env"]
        captured["args"] = args
        captured["config_path"] = f"{env['XDG_CONFIG_HOME']}/opencode/config.json"
        with open(captured["config_path"], encoding="utf-8") as handle:
            captured["config"] = handle.read()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        return mock_result

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "opencode_default_model": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
        }
        app.config["PROVIDER_URLS"] = {
            "ollama": "http://127.0.0.1:11434/api/chat",
        }
        with (
            patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run", side_effect=_fake_run),
        ):
            mock_settings.opencode_path = "opencode"
            mock_settings.opencode_default_model = "opencode/glm-5-free"
            mock_settings.default_provider = "ollama"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"

            rc, out, err = run_opencode_command("say hi")

    assert rc == 0
    assert out == "ok"
    assert err == ""
    assert captured["args"][:2] == [r"C:\tools\opencode.cmd", "run"]
    assert "--model" in captured["args"]
    model_index = captured["args"].index("--model")
    assert captured["args"][model_index + 1] == "ollama/lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest"
    assert '"baseURL": "http://127.0.0.1:11434/v1"' in captured["config"]
    assert '"lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest"' in captured["config"]


def test_resolve_opencode_runtime_config_respects_tool_mode_toolless(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "opencode_default_model": "lfm2.5-1.2b-glm-4.7-flash-thinking-i1:latest",
            "opencode_runtime": {"tool_mode": "toolless"},
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "ollama"
            mock_settings.opencode_default_model = "opencode/glm-5-free"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["tool_mode"] == "toolless"
    assert resolved["provider_config"]["default_agent"] == "ananta-worker"
    assert resolved["provider_config"]["agent"]["ananta-worker"]["tools"]["bash"] is False


def test_resolve_opencode_runtime_config_forces_target_provider_over_lmstudio_prefixed_model(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "default_model": "lmstudio/qwen2.5-coder-7b-instruct",
            "opencode_default_model": "lmstudio/qwen2.5-coder-7b-instruct",
            "opencode_runtime": {"tool_mode": "toolless", "execution_mode": "interactive_terminal", "target_provider": "ollama"},
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat", "lmstudio": "http://127.0.0.1:1234/v1"}
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.resolve_ollama_model", return_value="ananta-default"),
        ):
            mock_settings.default_provider = "lmstudio"
            mock_settings.default_model = "lmstudio/qwen2.5-coder-7b-instruct"
            mock_settings.opencode_default_model = "lmstudio/qwen2.5-coder-7b-instruct"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["target_provider"] == "ollama"
    assert resolved["model"] == "ollama/ananta-default"
    assert resolved["base_url"] == "http://127.0.0.1:11434/v1"


def test_resolve_opencode_runtime_config_normalizes_legacy_ollama_model(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "default_model": "ananta-default",
            "opencode_default_model": "ananta-default",
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "ollama"
            mock_settings.opencode_default_model = "ananta-default"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["model"] == "ollama/ananta-default"
    assert resolved["target_model"] == "ananta-default"


def test_resolve_opencode_runtime_config_resolves_short_ollama_model_to_installed_tag(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "default_model": "qwen2.5-coder:7b",
            "opencode_default_model": "qwen2.5-coder:7b",
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch(
                "agent.common.sgpt.resolve_ollama_model",
                return_value="bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest",
            ),
        ):
            mock_settings.default_provider = "ollama"
            mock_settings.opencode_default_model = "qwen2.5-coder:7b"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["model"] == "ollama/bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest"
    assert resolved["target_model"] == "bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest"


def test_resolve_opencode_runtime_config_falls_back_to_settings_provider_urls(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "opencode_default_model": "qwen2.5-coder:7b",
        }
        app.config["PROVIDER_URLS"] = {}
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch(
                "agent.common.sgpt.resolve_ollama_model",
                return_value="bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest",
            ),
        ):
            mock_settings.default_provider = "ollama"
            mock_settings.ollama_url = "http://ollama:11434/api/generate"
            mock_settings.opencode_default_model = "qwen2.5-coder:7b"
            mock_settings.http_timeout = 60
            resolved = resolve_opencode_runtime_config()

    assert resolved["base_url"] == "http://ollama:11434/v1"
    assert resolved["model"] == "ollama/bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s:latest"
    assert resolved["provider_config"]["model"] == resolved["model"]


def test_resolve_opencode_runtime_config_infers_local_provider_for_bare_opencode_model(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "opencode_default_model": "qwen2.5-coder:7b",
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch(
                "agent.common.sgpt.probe_ollama_runtime",
                return_value={"ok": True, "models": [{"name": "qwen2.5-coder:7b"}]},
            ),
            patch("agent.common.sgpt.resolve_ollama_model", return_value="qwen2.5-coder:7b"),
        ):
            mock_settings.default_provider = "ollama"
            mock_settings.default_model = "qwen2.5-coder:7b"
            mock_settings.opencode_default_model = "qwen2.5-coder:7b"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["target_provider"] == "ollama"
    assert resolved["target_model"] == "qwen2.5-coder:7b"
    assert resolved["model"] == "ollama/qwen2.5-coder:7b"
    assert resolved["base_url"] == "http://127.0.0.1:11434/v1"


def test_resolve_opencode_runtime_config_builds_lmstudio_provider_for_inferred_local_model(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "openai",
            "default_model": "gpt-4o",
            "opencode_default_model": "qwen2.5-coder:7b",
        }
        app.config["PROVIDER_URLS"] = {"lmstudio": "http://127.0.0.1:1234/v1"}
        with (
            patch("agent.common.sgpt.settings") as mock_settings,
            patch(
                "agent.common.sgpt.probe_lmstudio_runtime",
                return_value={"ok": True, "candidates": [{"id": "qwen2.5-coder-7b-instruct"}]},
            ),
        ):
            mock_settings.default_provider = "ollama"
            mock_settings.default_model = "qwen2.5-coder:7b"
            mock_settings.opencode_default_model = "qwen2.5-coder:7b"
            mock_settings.lmstudio_url = "http://127.0.0.1:1234/v1"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["target_provider"] == "lmstudio"
    assert resolved["target_model"] == "qwen2.5-coder-7b-instruct"
    assert resolved["model"] == "lmstudio/qwen2.5-coder-7b-instruct"
    assert resolved["base_url"] == "http://127.0.0.1:1234/v1"
    assert resolved["provider_config"]["model"] == "lmstudio/qwen2.5-coder-7b-instruct"


def test_resolve_opencode_runtime_config_defaults_to_general_model_for_ollama(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "default_model": "ananta-smoke",
            "model": "ananta-smoke",
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "ollama"
            mock_settings.default_model = "ananta-default"
            mock_settings.opencode_default_model = "opencode/glm-5-free"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["model"] == "ollama/ananta-smoke"
    assert resolved["target_model"] == "ananta-smoke"
    assert resolved["tool_mode"] == "toolless"
    assert resolved["provider_config"]["default_agent"] == "ananta-worker"


def test_resolve_opencode_runtime_config_forces_toolless_ollama_in_backend_mode(app):
    from agent.common.sgpt import resolve_opencode_runtime_config

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "ollama",
            "default_model": "ananta-smoke",
            "opencode_runtime": {"tool_mode": "full", "execution_mode": "backend"},
        }
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.default_provider = "ollama"
            mock_settings.default_model = "ananta-default"
            mock_settings.opencode_default_model = "opencode/glm-5-free"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            mock_settings.http_timeout = 30
            resolved = resolve_opencode_runtime_config()

    assert resolved["tool_mode"] == "toolless"
    assert resolved["provider_config"]["default_agent"] == "ananta-worker"


def test_build_default_agent_config_prefers_ollama_opencode_model(monkeypatch):
    from agent import config_defaults

    monkeypatch.setattr(config_defaults.settings, "default_provider", "ollama", raising=False)
    monkeypatch.setattr(config_defaults.settings, "default_model", "ananta-smoke", raising=False)
    monkeypatch.setattr(config_defaults.settings, "opencode_default_model", "opencode/glm-5-free", raising=False)

    cfg = config_defaults.build_default_agent_config()

    assert cfg["opencode_default_model"] == "qwen2.5-coder:7b"
    assert "qwen2.5-coder:7b" in cfg["autopilot_strategy_fallback_models"]
    assert cfg["opencode_runtime"]["tool_mode"] == "toolless"
    assert cfg["opencode_runtime"]["target_provider"] == "ollama"


def test_run_opencode_command_passes_workdir_to_subprocess(app):
    from agent.common.sgpt import run_opencode_command

    captured: dict = {}

    def _fake_run(args, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        captured["input"] = kwargs.get("input")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        return mock_result

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"default_provider": "ollama"}
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434/api/chat"}
        with (
            patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run", side_effect=_fake_run),
        ):
            mock_settings.opencode_path = "opencode"
            mock_settings.default_provider = "ollama"
            mock_settings.ollama_url = "http://127.0.0.1:11434/api/chat"
            run_opencode_command("say hi", workdir="/tmp/ananta-workdir")

    assert captured["cwd"] == "/tmp/ananta-workdir"
    assert captured["input"] == "say hi"


def test_run_opencode_command_uses_native_runtime_session(app):
    from agent.common.sgpt import run_opencode_command

    session = {"id": "cli-1", "metadata": {"opencode_runtime": {"kind": "native_server", "native_session_id": "ses-1"}}}
    runtime_service = MagicMock()
    runtime_service.run_session_turn.return_value = (0, "native-output", "")

    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
        patch("agent.services.opencode_runtime_service.get_opencode_runtime_service", return_value=runtime_service),
    ):
        rc, out, err = run_opencode_command("say hi", session=session)

    assert rc == 0
    assert out == "native-output"
    assert err == ""
    runtime_service.run_session_turn.assert_called_once()


def test_run_opencode_command_uses_live_terminal_session(app):
    from agent.common.sgpt import run_opencode_command

    session = {
        "id": "cli-1",
        "metadata": {
            "opencode_execution_mode": "live_terminal",
            "opencode_live_terminal": {"terminal_session_id": "cli-1", "forward_param": "cli-1"},
        },
    }
    runtime_service = MagicMock()
    runtime_service.run_opencode_turn.return_value = (0, "live-output", "")

    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
        patch("agent.services.live_terminal_session_service.get_live_terminal_session_service", return_value=runtime_service),
    ):
        rc, out, err = run_opencode_command("say hi", session=session, workdir="/tmp/live")

    assert rc == 0
    assert out == "live-output"
    assert err == ""
    runtime_service.run_opencode_turn.assert_called_once()
    call = runtime_service.run_opencode_turn.call_args
    assert call.args[0]["id"] == "cli-1"
    assert call.kwargs["workdir"] == "/tmp/live"


def test_run_opencode_command_uses_interactive_terminal_session(app):
    from agent.common.sgpt import run_opencode_command

    session = {
        "id": "cli-2",
        "metadata": {
            "opencode_execution_mode": "interactive_terminal",
            "opencode_live_terminal": {"terminal_session_id": "cli-2", "forward_param": "cli-2"},
        },
    }
    runtime_service = MagicMock()
    runtime_service.ensure_session_for_cli.return_value = {"terminal_session_id": "cli-2"}

    with (
        patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\opencode.cmd"),
        patch("agent.common.sgpt._run_opencode_subprocess", return_value=(0, '{"command":"echo ok"}', "", "opencode run")),
        patch("agent.services.live_terminal_session_service.get_live_terminal_session_service", return_value=runtime_service),
    ):
        rc, out, err = run_opencode_command("say hi", session=session, workdir="/tmp/interactive")

    assert rc == 0
    assert out == '{"command":"echo ok"}'
    assert err == ""
    runtime_service.ensure_session_for_cli.assert_called_once()
    runtime_service.append_output.assert_any_call("cli-2", "$ opencode run\n")
    runtime_service.append_output.assert_any_call("cli-2", '{"command":"echo ok"}\n')


def test_opencode_runtime_service_reuses_existing_server_without_deepcopying_process():
    from agent.services.opencode_runtime_service import OpencodeRuntimeService

    class NonCopyableProcess:
        pid = 4242

        def poll(self):
            return None

        def __deepcopy__(self, memo):
            raise TypeError("process_not_deepcopyable")

    service = OpencodeRuntimeService()
    session = {"id": "cli-1", "conversation_id": "role:po", "metadata": {"scope_key": "role:po"}}
    runtime_cfg = {"model": "ollama/example"}
    server_key = service._server_scope_key(session, runtime_cfg)
    service._servers[server_key] = {
        "server_key": server_key,
        "server_url": "http://127.0.0.1:4100",
        "port": 4100,
        "model": "ollama/example",
        "agent": "ananta-worker",
        "process": NonCopyableProcess(),
        "started_at": time.time(),
        "updated_at": time.time(),
    }

    reused = service._ensure_server(session, runtime_cfg)

    assert reused["server_key"] == server_key
    assert reused["server_url"] == "http://127.0.0.1:4100"
    assert reused["pid"] == 4242
    assert "process" not in reused


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
    assert preflight["providers"]["codex"]["target_kind"] in {"local_openai", "remote_openai_compatible", "remote_ananta_hub"}


def test_get_cli_backend_preflight_normalizes_lmstudio_models_url_input(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {"prefer_lmstudio": True},
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://127.0.0.1:1234/v1/models",
        }
        with (
            patch("agent.common.sgpt.shutil.which", return_value=None),
            patch("agent.llm_integration.probe_lmstudio_runtime", return_value={
                "ok": True,
                "status": "ok",
                "base_url": "http://127.0.0.1:1234/v1",
                "models_url": "http://127.0.0.1:1234/v1/models",
                "candidate_count": 1,
                "candidates": [{"id": "model-a"}],
            }),
            patch("agent.common.sgpt.settings") as mock_settings,
        ):
            mock_settings.codex_path = "codex"
            mock_settings.opencode_path = "opencode"
            mock_settings.aider_path = "aider"
            mock_settings.mistral_code_path = "mistral-code"
            mock_settings.default_provider = "lmstudio"
            mock_settings.lmstudio_url = "http://wrong-host:1234/v1/chat/completions"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None
            mock_settings.http_timeout = 5.0

            preflight = get_cli_backend_preflight()

    assert preflight["providers"]["lmstudio"]["base_url"] == "http://127.0.0.1:1234/v1"
    assert preflight["providers"]["lmstudio"]["models_url"] == "http://127.0.0.1:1234/v1/models"
    assert preflight["providers"]["codex"]["base_url"] == "http://127.0.0.1:1234/v1"


def test_get_cli_backend_preflight_reports_not_configured_lmstudio_provider(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {"prefer_lmstudio": True},
        }
        app.config["PROVIDER_URLS"] = {}
        with patch("agent.common.sgpt.shutil.which", return_value=None), patch("agent.common.sgpt.settings") as mock_settings:
            mock_settings.codex_path = "codex"
            mock_settings.opencode_path = "opencode"
            mock_settings.aider_path = "aider"
            mock_settings.mistral_code_path = "mistral-code"
            mock_settings.default_provider = "lmstudio"
            mock_settings.lmstudio_url = ""
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None
            mock_settings.http_timeout = 5.0

            preflight = get_cli_backend_preflight()

    assert preflight["providers"]["lmstudio"]["configured"] is False
    assert preflight["providers"]["lmstudio"]["status"] == "not_configured"
    assert preflight["providers"]["lmstudio"]["reachable"] is False
    assert preflight["providers"]["lmstudio"]["models_url"] is None


def test_get_cli_backend_preflight_reports_invalid_lmstudio_url(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {"prefer_lmstudio": True},
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "not-a-valid-url",
        }
        with (
            patch("agent.common.sgpt.shutil.which", return_value=None),
            patch("agent.llm_integration.probe_lmstudio_runtime", return_value={
                "ok": False,
                "status": "invalid_url",
                "base_url": "not-a-valid-url",
                "models_url": None,
                "candidate_count": 0,
                "candidates": [],
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

    assert preflight["providers"]["lmstudio"]["configured"] is True
    assert preflight["providers"]["lmstudio"]["status"] == "invalid_url"
    assert preflight["providers"]["lmstudio"]["reachable"] is False
    assert preflight["providers"]["lmstudio"]["base_url"] == "not-a-valid-url"


def test_get_cli_backend_preflight_reports_reachable_runtime_without_models(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "codex_cli": {"prefer_lmstudio": True},
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://127.0.0.1:1234/v1",
        }
        with (
            patch("agent.common.sgpt.shutil.which", return_value=None),
            patch("agent.llm_integration.probe_lmstudio_runtime", return_value={
                "ok": True,
                "status": "reachable_no_models",
                "base_url": "http://127.0.0.1:1234/v1",
                "models_url": "http://127.0.0.1:1234/v1/models",
                "candidate_count": 0,
                "candidates": [],
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

    assert preflight["providers"]["lmstudio"]["configured"] is True
    assert preflight["providers"]["lmstudio"]["status"] == "reachable_no_models"
    assert preflight["providers"]["lmstudio"]["reachable"] is True
    assert preflight["providers"]["lmstudio"]["candidate_count"] == 0


def test_get_cli_backend_preflight_includes_ollama_activity_and_gpu_usage(app):
    from agent.common.sgpt import get_cli_backend_preflight

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"default_provider": "ollama"}
        app.config["PROVIDER_URLS"] = {"ollama": "http://127.0.0.1:11434"}
        with (
            patch("agent.common.sgpt.shutil.which", return_value=None),
            patch("agent.llm_integration.probe_ollama_runtime", return_value={
                "ok": True,
                "status": "ok",
                "base_url": "http://127.0.0.1:11434",
                "tags_url": "http://127.0.0.1:11434/api/tags",
                "candidate_count": 1,
                "models": [{"name": "glm-4.7"}],
            }),
            patch("agent.llm_integration.probe_ollama_activity", return_value={
                "ok": True,
                "status": "ok",
                "base_url": "http://127.0.0.1:11434",
                "ps_url": "http://127.0.0.1:11434/api/ps",
                "active_count": 1,
                "gpu_active": True,
                "executor_summary": {"gpu": 1, "cpu": 0, "unknown": 0},
                "active_models": [{"name": "glm-4.7", "executor": "gpu"}],
            }),
            patch("agent.common.sgpt.settings") as mock_settings,
        ):
            mock_settings.codex_path = "codex"
            mock_settings.opencode_path = "opencode"
            mock_settings.aider_path = "aider"
            mock_settings.mistral_code_path = "mistral-code"
            mock_settings.default_provider = "ollama"
            mock_settings.lmstudio_url = ""
            mock_settings.ollama_url = "http://127.0.0.1:11434"
            mock_settings.openai_url = "https://api.openai.com/v1/chat/completions"
            mock_settings.openai_api_key = None
            mock_settings.http_timeout = 5.0

            preflight = get_cli_backend_preflight()

    ollama = (preflight.get("providers") or {}).get("ollama") or {}
    assert ollama.get("configured") is True
    assert ollama.get("reachable") is True
    assert ollama.get("candidate_count") == 1
    activity = ollama.get("activity") or {}
    assert activity.get("active_count") == 1
    assert activity.get("gpu_active") is True
    assert (activity.get("executor_summary") or {}).get("gpu") == 1


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
        with patch("agent.common.sgpt.settings") as mock_settings:
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
        with patch("agent.common.sgpt.settings") as mock_settings:
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
            patch("agent.common.sgpt.shutil.which", return_value=r"C:\tools\codex.cmd"),
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.subprocess.run") as mock_run,
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
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.run_codex_command", return_value=(-1, "", "codex unavailable")),
            patch("agent.common.sgpt.run_opencode_command", return_value=(0, "ok via opencode", "")),
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
            patch("agent.common.sgpt.settings") as mock_settings,
            patch("agent.common.sgpt.run_codex_command") as mock_codex,
            patch("agent.common.sgpt.run_opencode_command", return_value=(0, "ok after cooldown skip", "")),
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
        patch("agent.common.sgpt.settings") as mock_settings,
        patch("agent.common.sgpt.run_opencode_command", return_value=(0, "ok", "")) as mock_run_opencode,
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
