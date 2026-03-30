from tests.test_live_agent_chain_e2e import _live_llm_base_url_candidates, _live_llm_models_url


def test_live_agent_chain_candidates_include_cross_environment_ollama_urls(monkeypatch):
    monkeypatch.setenv("LIVE_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://custom-ollama:11434/api/generate")
    monkeypatch.delenv("E2E_OLLAMA_URL", raising=False)

    candidates = _live_llm_base_url_candidates()

    assert candidates[0] == "http://custom-ollama:11434/api/generate"
    assert "http://localhost:11434/api/generate" in candidates
    assert "http://127.0.0.1:11434/api/generate" in candidates
    assert "http://host.docker.internal:11434/api/generate" in candidates


def test_live_agent_chain_models_url_normalizes_lmstudio_variants(monkeypatch):
    monkeypatch.setenv("LIVE_LLM_PROVIDER", "lmstudio")

    assert _live_llm_models_url("http://localhost:1234/v1") == "http://localhost:1234/v1/models"
    assert _live_llm_models_url("http://localhost:1234/v1/chat/completions") == "http://localhost:1234/v1/models"
