from agent.cli_backends.sgpt import get_cli_backend_preflight


def test_worker_runtime_scope_skips_lmstudio_and_ollama_probes(monkeypatch):
    called = {"lmstudio": 0, "ollama": 0, "activity": 0}

    def _lmstudio(*args, **kwargs):
        called["lmstudio"] += 1
        return {"ok": True, "status": "ok", "candidate_count": 1, "candidates": []}

    def _ollama(*args, **kwargs):
        called["ollama"] += 1
        return {"ok": True, "status": "ok", "candidate_count": 1, "models": []}

    def _activity(*args, **kwargs):
        called["activity"] += 1
        return {"ok": True, "status": "ok", "active_count": 0}

    monkeypatch.setattr("agent.llm_integration.probe_lmstudio_runtime", _lmstudio)
    monkeypatch.setattr("agent.llm_integration.probe_ollama_runtime", _ollama)
    monkeypatch.setattr("agent.llm_integration.probe_ollama_activity", _activity)

    preflight = get_cli_backend_preflight(runtime_scope="worker")
    providers = preflight.get("providers") or {}
    assert providers["lmstudio"]["probe_skipped"] is True
    assert providers["ollama"]["probe_skipped"] is True
    assert called == {"lmstudio": 0, "ollama": 0, "activity": 0}
