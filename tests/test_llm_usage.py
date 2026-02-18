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
            "llm_config": {"provider": "openai", "model": "gpt-4o", "base_url": "https://api.openai.com/v1/chat/completions"},
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
