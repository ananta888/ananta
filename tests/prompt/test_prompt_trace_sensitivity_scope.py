from types import SimpleNamespace

from agent.llm_integration import _call_llm


def test_llm_scope_local_for_ollama(monkeypatch):
    captured = {}

    class _Svc:
        def create_trace(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(trace_id="pt1")

        def finalize_trace(self, trace, **kwargs):
            return trace

        def store(self, trace):
            return None

    monkeypatch.setattr("agent.services.prompt_trace_service.get_prompt_trace_service", lambda: _Svc())
    monkeypatch.setattr("agent.llm_integration._execute_llm_call", lambda **kwargs: {"text": "ok", "usage": {}})
    monkeypatch.setattr("agent.llm_integration._report_llm_success", lambda *_: None)
    monkeypatch.setattr("agent.llm_integration.log_llm_entry", lambda **_: None)

    _call_llm(
        provider="ollama",
        model="qwen",
        prompt="hello",
        urls={"ollama": "http://localhost:11434/api/generate"},
        api_key=None,
        timeout=3,
        history=[],
        max_retries=0,
        backoff_factor=1,
        idempotency_key="k1",
    )

    assert captured.get("llm_scope") == "local_only"
    assert captured.get("sensitivity_level") == "internal"
