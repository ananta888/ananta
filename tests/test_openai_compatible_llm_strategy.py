from agent.llm_strategies import OpenAIStrategy, get_strategy


def test_local_openai_compatible_provider_ids_use_standard_transport():
    assert isinstance(get_strategy("llamacpp"), OpenAIStrategy)
    assert isinstance(get_strategy("openai_compatible"), OpenAIStrategy)


def test_an_api_base_is_called_at_the_url_the_endpoint_policy_checked(monkeypatch):
    from unittest.mock import MagicMock

    from agent.llm_strategies import standard

    calls = []

    def post(url, payload, **kwargs):
        calls.append(url)
        response = MagicMock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return response

    monkeypatch.setattr(standard, "_http_post", post)
    strategy = standard.OpenAIStrategy()
    for url in ("http://host.docker.internal:18150/v1", "http://host.docker.internal:18150/v1/chat/completions"):
        strategy.execute("m", "hi", url, None, None, 5, provider="llamacpp")
    assert calls == ["http://host.docker.internal:18150/v1/chat/completions"] * 2
