from __future__ import annotations

from agent.llm_strategies.standard import OllamaStrategy, OpenAIStrategy


class _Response:
    status_code = 200
    text = "ok"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_openai_compatible_strategy_forwards_sampling_seed(monkeypatch):
    captured = {}

    def post(_url, payload, **_kwargs):
        captured.update(payload)
        return _Response({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("agent.llm_strategies.standard._http_post", post)
    OpenAIStrategy().execute(
        model="model", prompt="hello", url="http://local/v1/chat/completions",
        api_key=None, history=None, timeout=10, seed=41,
    )
    assert captured["seed"] == 41


def test_ollama_strategy_forwards_sampling_seed(monkeypatch):
    captured = {}

    def post(_url, payload, **_kwargs):
        captured.update(payload)
        return _Response({"response": "ok"})

    monkeypatch.setattr("agent.llm_strategies.standard._http_post", post)
    OllamaStrategy().execute(
        model="model", prompt="hello", url="http://ollama/api/generate",
        api_key=None, history=None, timeout=10, seed=17,
    )
    assert captured["options"]["seed"] == 17
