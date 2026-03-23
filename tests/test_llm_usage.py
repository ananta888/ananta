from unittest.mock import patch

from flask import g

from agent.tool_guardrails import ToolGuardrailDecision


def test_extract_llm_text_and_usage_from_strategy_result():
    from agent.llm_integration import extract_llm_text_and_usage

    result = {"text": "hello", "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}}
    text, usage = extract_llm_text_and_usage(result)
    assert text == "hello"
    assert usage["prompt_tokens"] == 12
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 17


def test_update_lmstudio_history_delegates_to_record_helper():
    from agent.llm_integration import _update_lmstudio_history

    with patch("agent.llm_integration.update_json") as mock_update_json:
        with patch("agent.llm_integration._record_lmstudio_result", return_value={"models": {}}) as mock_record:
            _update_lmstudio_history("model-a", True)
            update_callback = mock_update_json.call_args.args[1]
            update_callback({"models": {}})

    mock_record.assert_called_once_with({"models": {}}, "model-a", True)


def test_prepare_lmstudio_history_touches_and_persists_models():
    from agent.llm_integration import _prepare_lmstudio_history

    candidates = [{"id": "model-a"}, {"id": "model-b"}]
    with patch("agent.llm_integration._load_lmstudio_history", return_value={"models": {}}) as mock_load:
        with patch("agent.llm_integration._touch_lmstudio_models", wraps=lambda history, _: {"models": history["models"]}) as mock_touch:
            with patch("agent.llm_integration._save_lmstudio_history") as mock_save:
                history = _prepare_lmstudio_history(candidates)

    assert history == {"models": {}}
    mock_load.assert_called_once()
    mock_touch.assert_called_once_with({"models": {}}, ["model-a", "model-b"])
    mock_save.assert_called_once_with({"models": {}})


def test_call_llm_stores_usage_in_request_context(app):
    from flask import g

    from agent.llm_integration import _call_llm

    with app.test_request_context("/llm/generate", method="POST"):
        with patch("agent.llm_integration._execute_llm_call") as mock_exec:
            mock_exec.return_value = {
                "text": "ok",
                "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            }
            with patch("agent.llm_integration.settings") as mock_settings:
                mock_settings.retry_count = 0
                mock_settings.retry_backoff = 0.0
                out = _call_llm("openai", "m", "p", {"openai": "http://x"}, "k")

        assert out == "ok"
        assert g.llm_last_usage["prompt_tokens"] == 11
        assert g.llm_last_usage["completion_tokens"] == 4
        assert g.llm_last_usage["total_tokens"] == 15


def test_llm_generate_prefers_provider_usage_for_guardrail_tokens(client, app):
    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "llm_config": {"provider": "ollama", "base_url": "http://localhost:11434/api/generate", "model": "m1"},
            "llm_tool_guardrails": {"enabled": True},
        }

    seen: dict = {}

    def _fake_generate_text(*args, **kwargs):
        g.llm_last_usage = {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}
        return '{"tool_calls":[{"name":"create_team","args":{"name":"A","team_type":"Scrum"}}],"answer":"ok"}'

    def _fake_guardrails(tool_calls, cfg, token_usage=None):
        seen["token_usage"] = token_usage or {}
        return ToolGuardrailDecision(
            allowed=False,
            blocked_tools=["create_team"],
            reasons=["guardrail_test_block"],
            details={"token_source": (token_usage or {}).get("token_source")},
        )

    with patch("agent.routes.config.generate_text", side_effect=_fake_generate_text):
        with patch("agent.routes.config.evaluate_tool_call_guardrails", side_effect=_fake_guardrails):
            res = client.post(
                "/llm/generate",
                json={"prompt": "create scrum team please", "confirm_tool_calls": True},
                headers={"Authorization": "Bearer secret-token"},
            )

    assert res.status_code == 200
    data = res.json["data"]
    assert "create_team" in (data.get("blocked_tools") or [])
    assert "guardrail_test_block" in (data.get("blocked_reasons") or [])
    assert seen["token_usage"]["token_source"] == "provider_usage"
    assert seen["token_usage"]["provider_usage"]["total_tokens"] == 133
    assert seen["token_usage"]["estimated_total_tokens"] == 133


def test_llm_generate_returns_routing_metadata(client, app):
    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["PROVIDER_URLS"] = {"lmstudio": "http://127.0.0.1:1234/v1"}
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "llm_config": {"provider": "ollama", "model": "llama3"},
            "default_provider": "lmstudio",
            "default_model": "model-default",
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}'):
        res = client.post(
            "/llm/generate",
            json={"prompt": "hello", "config": {"provider": "lmstudio", "model": "model-x"}},
            headers={"Authorization": "Bearer secret-token"},
        )

    assert res.status_code == 200
    data = res.json["data"]
    routing = data.get("routing") or {}
    assert routing.get("policy_version") == "llm-generate-v1"
    assert (routing.get("requested") or {}).get("provider") == "lmstudio"
    assert (routing.get("effective") or {}).get("provider") == "lmstudio"
    assert (routing.get("effective") or {}).get("model") == "model-x"
    assert (routing.get("fallback") or {}).get("provider_source") == "request.config.provider"


def test_llm_generate_error_response_contains_routing_metadata(client, app):
    with app.app_context():
        cfg = app.config.get("AGENT_CONFIG", {}) or {}
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            **cfg,
            "llm_config": {
                "provider": "openai",
                "model": "gpt-4o",
                "base_url": "https://api.openai.com/v1/chat/completions",
            },
        }

    res = client.post(
        "/llm/generate",
        json={"prompt": "hello"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert res.status_code == 400
    assert res.json["message"] == "llm_api_key_missing"
    routing = (res.json.get("data") or {}).get("routing") or {}
    assert routing.get("policy_version") == "llm-generate-v1"
    assert (routing.get("effective") or {}).get("provider") == "openai"


def test_llm_generate_missing_prompt_contains_routing_metadata(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"

    res = client.post(
        "/llm/generate",
        json={},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert res.status_code == 400
    assert res.json["message"] == "missing_prompt"
    routing = (res.json.get("data") or {}).get("routing") or {}
    assert routing.get("policy_version") == "llm-generate-v1"
    assert (routing.get("fallback") or {}).get("provider_source") == "preflight_validation"


def test_llm_generate_invalid_json_contains_routing_metadata(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"

    res = client.post(
        "/llm/generate",
        data="[]",
        content_type="application/json",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert res.status_code == 400
    assert res.json["message"] == "invalid_json"
    routing = (res.json.get("data") or {}).get("routing") or {}
    assert routing.get("policy_version") == "llm-generate-v1"
    assert (routing.get("fallback") or {}).get("provider_source") == "preflight_validation"


def test_llm_generate_forwards_temperature_and_context_limit_from_request(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            "llm_config": {"provider": "lmstudio", "model": "m1", "base_url": "http://127.0.0.1:1234/v1"}
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post(
            "/llm/generate",
            json={"prompt": "hello", "config": {"temperature": 0.7, "context_limit": 8192}},
            headers={"Authorization": "Bearer secret-token"},
        )

    assert res.status_code == 200
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["temperature"] == 0.7
    assert kwargs["max_context_tokens"] == 8192


def test_llm_generate_uses_llm_config_temperature_and_context_limit_fallback(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            "llm_config": {
                "provider": "lmstudio",
                "model": "m1",
                "base_url": "http://127.0.0.1:1234/v1",
                "temperature": 0.3,
                "context_limit": 4096,
            }
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post(
            "/llm/generate",
            json={"prompt": "hello"},
            headers={"Authorization": "Bearer secret-token"},
        )

    assert res.status_code == 200
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["temperature"] == 0.3
    assert kwargs["max_context_tokens"] == 4096


def test_llm_generate_uses_api_key_profile_for_codex(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["PROVIDER_URLS"] = {"openai": "https://api.openai.com/v1/chat/completions"}
        app.config["AGENT_CONFIG"] = {
            "llm_api_key_profiles": {"codex-main": {"provider": "codex", "api_key": "sk-profile"}},
            "llm_config": {"provider": "codex", "model": "gpt-5-codex", "api_key_profile": "codex-main"},
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post("/llm/generate", json={"prompt": "hello"}, headers={"Authorization": "Bearer secret-token"})

    assert res.status_code == 200
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["provider"] == "codex"
    assert kwargs["api_key"] == "sk-profile"


def test_llm_generate_uses_openai_profile_alias_for_codex(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["PROVIDER_URLS"] = {"openai": "https://api.openai.com/v1/chat/completions"}
        app.config["AGENT_CONFIG"] = {
            "llm_api_key_profiles": {"shared-openai": {"provider": "openai", "api_key": "sk-openai-shared"}},
            "llm_config": {"provider": "codex", "model": "gpt-5-codex", "api_key_profile": "shared-openai"},
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post("/llm/generate", json={"prompt": "hello"}, headers={"Authorization": "Bearer secret-token"})

    assert res.status_code == 200
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["provider"] == "codex"
    assert kwargs["api_key"] == "sk-openai-shared"


def test_llm_generate_resolves_codex_base_url_from_openai_alias(client, app):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["PROVIDER_URLS"] = {"openai": "https://api.openai.com/v1/chat/completions"}
        app.config["AGENT_CONFIG"] = {
            "llm_api_key_profiles": {"shared-openai": {"provider": "openai", "api_key": "sk-openai-shared"}},
            "llm_config": {"provider": "codex", "model": "gpt-5-codex", "api_key_profile": "shared-openai"},
        }

    with patch("agent.routes.config.generate_text", return_value='{"answer":"ok","tool_calls":[]}') as mock_generate:
        res = client.post("/llm/generate", json={"prompt": "hello"}, headers={"Authorization": "Bearer secret-token"})

    assert res.status_code == 200
    kwargs = mock_generate.call_args.kwargs
    assert kwargs["provider"] == "codex"
    assert kwargs["base_url"] == "https://api.openai.com/v1/chat/completions"


def test_generate_text_prefers_runtime_app_state_over_settings_defaults(app):
    from agent.llm_integration import generate_text

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_provider": "lmstudio",
            "default_model": "runtime-model",
        }
        app.config["PROVIDER_URLS"] = {
            "lmstudio": "http://127.0.0.1:1234/v1",
            "openai": "https://wrong.example/v1/chat/completions",
            "codex": "https://wrong.example/v1/chat/completions",
            "anthropic": "https://anthropic.example/v1/messages",
        }
        with patch("agent.llm_integration._call_llm", return_value="ok") as mock_call:
            with patch("agent.llm_integration.settings") as mock_settings:
                mock_settings.default_provider = "openai"
                mock_settings.default_model = "settings-model"
                mock_settings.ollama_url = "http://localhost:11434/api/generate"
                mock_settings.lmstudio_url = "http://wrong-host:1234/v1"
                mock_settings.openai_url = "https://wrong.example/v1/chat/completions"
                mock_settings.anthropic_url = "https://anthropic.example/v1/messages"
                mock_settings.mock_url = "http://mock"
                mock_settings.openai_api_key = None
                mock_settings.anthropic_api_key = None

                out = generate_text("hello")

    assert out == "ok"
    assert mock_call.call_args.args[0] == "lmstudio"
    assert mock_call.call_args.args[1] == "runtime-model"
    assert mock_call.call_args.args[3]["lmstudio"] == "http://127.0.0.1:1234/v1"


def test_probe_lmstudio_runtime_reports_models_url_and_candidates():
    from agent.llm_integration import probe_lmstudio_runtime

    with patch("agent.llm_integration._http_get", return_value={
        "data": [
            {"id": "model-a", "context_length": 32768},
            {"id": "embed-model", "context_length": 8192},
        ]
    }):
        result = probe_lmstudio_runtime("http://127.0.0.1:1234/v1/chat/completions", timeout=5)

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["models_url"] == "http://127.0.0.1:1234/v1/models"
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["id"] == "model-a"


def test_lmstudio_strategy_prefers_runtime_default_model_over_settings_default(app):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_model": "runtime-model",
        }
        with patch.object(strategy, "_list_lmstudio_candidates", return_value=[]), patch.object(
            strategy,
            "_call_with_model",
            return_value={"text": "ok", "usage": {}},
        ) as mock_call, patch("agent.llm_strategies.lmstudio.settings") as mock_settings:
            mock_settings.default_model = "settings-model"
            mock_settings.lmstudio_api_mode = "chat"
            mock_settings.lmstudio_max_context_tokens = 4096

            result = strategy.execute(
                model="",
                prompt="hello",
                url="http://127.0.0.1:1234/v1",
                api_key=None,
                history=None,
                timeout=5,
            )

    assert result == {"text": "ok", "usage": {}}
    assert mock_call.call_args.args[0] == "runtime-model"


def test_lmstudio_strategy_keeps_explicit_model_when_candidates_are_unavailable(app):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            "default_model": "runtime-model",
        }
        with patch.object(strategy, "_list_lmstudio_candidates", return_value=[]), patch.object(
            strategy,
            "_call_with_model",
            return_value={"text": "ok", "usage": {}},
        ) as mock_call, patch("agent.llm_strategies.lmstudio.settings") as mock_settings:
            mock_settings.default_model = "settings-model"
            mock_settings.lmstudio_api_mode = "chat"
            mock_settings.lmstudio_max_context_tokens = 4096

            result = strategy.execute(
                model="explicit-model",
                prompt="hello",
                url="http://127.0.0.1:1234/v1",
                api_key=None,
                history=None,
                timeout=5,
            )

    assert result == {"text": "ok", "usage": {}}
    assert mock_call.call_args.args[0] == "explicit-model"


def test_lmstudio_strategy_falls_back_to_next_candidate_when_first_returns_empty_text(app):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()
    candidates = [
        {"id": "model-a", "context_length": 4096},
        {"id": "model-b", "context_length": 8192},
    ]

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"default_model": "runtime-model"}
        with patch.object(strategy, "_list_lmstudio_candidates", return_value=candidates), patch.object(
            strategy,
            "_prepare_lmstudio_history",
            return_value={"models": {}},
        ), patch.object(
            strategy,
            "_load_lmstudio_history",
            return_value={"models": {}},
        ), patch.object(
            strategy,
            "_select_best_lmstudio_model",
            side_effect=[candidates[0], candidates[1]],
        ), patch.object(
            strategy,
            "_call_with_model",
            side_effect=[
                {"text": "", "usage": {}},
                {"text": "ok-from-b", "usage": {"total_tokens": 12}},
            ],
        ) as mock_call, patch("agent.llm_strategies.lmstudio.settings") as mock_settings:
            mock_settings.default_model = "settings-model"
            mock_settings.lmstudio_api_mode = "chat"
            mock_settings.lmstudio_max_context_tokens = 4096

            result = strategy.execute(
                model="auto",
                prompt="hello",
                url="http://127.0.0.1:1234/v1",
                api_key=None,
                history=None,
                timeout=5,
            )

    assert result == {"text": "ok-from-b", "usage": {"total_tokens": 12}}
    assert mock_call.call_count == 2
    assert mock_call.call_args_list[0].args[0] == "model-a"
    assert mock_call.call_args_list[1].args[0] == "model-b"


def test_lmstudio_strategy_returns_empty_when_all_candidates_return_empty_text(app):
    from agent.llm_strategies.lmstudio import LMStudioStrategy

    strategy = LMStudioStrategy()
    candidates = [
        {"id": "model-a", "context_length": 4096},
        {"id": "model-b", "context_length": 8192},
    ]

    with app.app_context():
        app.config["AGENT_CONFIG"] = {"default_model": "runtime-model"}
        with patch.object(strategy, "_list_lmstudio_candidates", return_value=candidates), patch.object(
            strategy,
            "_prepare_lmstudio_history",
            return_value={"models": {}},
        ), patch.object(
            strategy,
            "_load_lmstudio_history",
            return_value={"models": {}},
        ), patch.object(
            strategy,
            "_select_best_lmstudio_model",
            side_effect=[candidates[0], candidates[1]],
        ), patch.object(
            strategy,
            "_call_with_model",
            side_effect=[
                {"text": "", "usage": {}},
                {"text": "   ", "usage": {}},
            ],
        ) as mock_call, patch("agent.llm_strategies.lmstudio.settings") as mock_settings:
            mock_settings.default_model = "settings-model"
            mock_settings.lmstudio_api_mode = "chat"
            mock_settings.lmstudio_max_context_tokens = 4096

            result = strategy.execute(
                model="auto",
                prompt="hello",
                url="http://127.0.0.1:1234/v1",
                api_key=None,
                history=None,
                timeout=5,
            )

    assert result == ""
    assert mock_call.call_count == 2
