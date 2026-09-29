from types import SimpleNamespace

from agent.llm_integration import _call_llm


def test_llm_integration_trace_contains_context_sources_and_scope(monkeypatch):
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
        provider="openai",
        model="gpt-test",
        prompt="hello",
        urls={"openai": "https://api.openai.com/v1"},
        api_key=None,
        timeout=3,
        history=[{"role": "user", "content": "prev"}],
        max_retries=0,
        backoff_factor=1,
        idempotency_key="k1",
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
    )

    assert captured.get("context_sources")
    assert captured.get("llm_scope") == "external_cloud_allowed"
