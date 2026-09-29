from __future__ import annotations

from agent.llm_strategies.standard import OllamaStrategy


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {
            "response": "ok",
            "prompt_eval_count": 12,
            "eval_count": 3,
        }


def test_ollama_strategy_sends_context_output_and_temperature_options(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_http_post(url, payload, **kwargs):
        captured["url"] = url
        captured["payload"] = payload
        captured["kwargs"] = kwargs
        return _FakeResponse()

    monkeypatch.setattr("agent.llm_strategies.standard._http_post", _fake_http_post)

    result = OllamaStrategy().execute(
        model="ananta-default",
        prompt="Antworte kurz.",
        url="http://ollama:11434/api/generate",
        api_key=None,
        history=None,
        timeout=30,
        max_context_tokens=65536,
        max_output_tokens=4096,
        temperature=0.2,
    )

    assert result == {
        "text": "ok",
        "usage": {"prompt_eval_count": 12, "eval_count": 3},
    }
    assert captured["url"] == "http://ollama:11434/api/generate"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "ananta-default"
    assert payload["prompt"] == "Antworte kurz."
    assert payload["stream"] is False
    assert payload["options"] == {
        "num_ctx": 65536,
        "num_predict": 4096,
        "temperature": 0.2,
    }
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["return_response"] is True
