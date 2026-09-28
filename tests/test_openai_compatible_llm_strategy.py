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


def test_local_llamacpp_requests_are_trimmed_to_anantas_32k_window(monkeypatch):
    from unittest.mock import MagicMock

    from agent.llm_strategies import standard

    sent = []

    def post(url, payload, **kwargs):
        sent.append(payload["messages"])
        response = MagicMock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return response

    monkeypatch.setattr(standard, "_http_post", post)
    history = [{"role": "user", "content": "x" * 40_000}, {"role": "assistant", "content": "y" * 40_000}] * 3
    strategy = standard.OpenAIStrategy()

    def budget(messages):
        return sum(strategy._estimate_tokens(str(m.get("content", ""))) for m in messages)

    strategy.execute("m", "latest question", "http://h/v1", None, history, 5, provider="llamacpp")
    assert budget(sent[-1]) <= 32768 and sent[-1][-1]["content"] == "latest question"  # newest turn kept
    strategy.execute("m", "q", "http://h/v1", None, history, 5, provider="llamacpp", max_context_tokens=8192)
    assert budget(sent[-1]) <= 8192
    strategy.execute("m", "q", "https://api.openai.com/v1", None, history, 5, provider="openai")
    assert budget(sent[-1]) <= 32768  # Ananta's configured window is the upper bound for every provider


def test_the_default_context_is_32k_everywhere():
    from pathlib import Path

    from agent.config import settings
    from agent.config_defaults import build_default_agent_config

    assert settings.default_context_tokens == 32768
    assert build_default_agent_config()["llm_config"]["context_limit"] == 32768
    modelfile = Path(__file__).resolve().parents[1] / "autoimport-state/modelfiles/ananta-default.Modelfile"
    assert "PARAMETER num_ctx 32768" in modelfile.read_text(encoding="utf-8")
