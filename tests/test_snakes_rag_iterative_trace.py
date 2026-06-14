from __future__ import annotations

class _RecordingTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, phase: str, title: str, **kwargs) -> None:
        self.events.append({"phase": phase, "title": title, **kwargs})


def test_rag_iterative_trace_records_full_batch_prompt_before_recorder_limit(tmp_path, monkeypatch):
    source = tmp_path / "docs" / "system-komponenten.md"
    source.parent.mkdir()
    long_content = "CodeCompass ist das RAG-Indexierungs- und Retrieval-System von Ananta. " * 40
    source.write_text(long_content, encoding="utf-8")

    from agent.routes import snakes_rag_iterative as mod

    monkeypatch.setattr(
        mod,
        "_current_config",
        lambda: {
            "chat_full_scan_chars_per_file": 20_000,
            "rag_iterative_tool_calls_enabled": False,
        },
    )
    monkeypatch.setattr(mod._cfg_settings, "rag_repo_root", str(tmp_path), raising=False)
    monkeypatch.setattr(mod, "lookup_model_context_tokens", lambda _model: 16_000)

    class _Chunk:
        source = "docs/system-komponenten.md"
        score = 99.0

    class _FakeRepositoryMapEngine:
        def __init__(self, _repo_root):
            pass

        def search(self, *_args, **_kwargs):
            return [_Chunk()]

    monkeypatch.setattr("agent.hybrid_orchestrator.RepositoryMapEngine", _FakeRepositoryMapEngine)
    captured_calls: list[dict] = []

    def _fake_generate_text(**kwargs):
        captured_calls.append(dict(kwargs))
        return "batch answer"

    monkeypatch.setattr(mod, "generate_text", _fake_generate_text)

    rec = _RecordingTrace()
    answer, trace = mod.worker_chat_rag_iterative(
        "was ist CodeCompass",
        rec=rec,
        conversation_history=[{"role": "assistant", "content": "Vorherige Antwort"}],
    )

    batch_event = next(event for event in rec.events if event["phase"] == "rag_iterative_batch_1")
    assert answer == "batch answer"
    assert trace["batches_completed"] == 1
    assert trace["conversation_history_messages"] == 1
    assert captured_calls[0]["history"] == [
        {"role": "system", "content": mod._SYSTEM_PROMPT},
        {"role": "assistant", "content": "Vorherige Antwort"},
    ]
    assert len(batch_event["input_preview"]) > 800
    assert long_content[:900] in batch_event["input_preview"]


def test_rag_iterative_tool_mode_filters_generated_codecompass_outputs(tmp_path, monkeypatch):
    source = tmp_path / "agent" / "codecompass_context.py"
    source.parent.mkdir()
    source.write_text("def explain_codecompass():\n    return 'source'\n", encoding="utf-8")
    generated = tmp_path / "rag-helper" / "out" / "index_by_kind"
    generated.mkdir(parents=True)
    (generated / "python_module_summary.jsonl").write_text("generated", encoding="utf-8")

    from agent.routes import snakes_rag_iterative as mod

    monkeypatch.setattr(
        mod,
        "_current_config",
        lambda: {
            "chat_full_scan_chars_per_file": 20_000,
            "rag_iterative_tool_calls_enabled": True,
            "rag_iterative_initial_min_files": 1,
            "rag_iterative_initial_max_files": 2,
        },
    )
    monkeypatch.setattr(mod._cfg_settings, "rag_repo_root", str(tmp_path), raising=False)
    monkeypatch.setattr(mod, "lookup_model_context_tokens", lambda _model: 16_000)

    class _Chunk:
        def __init__(self, source: str, score: float) -> None:
            self.source = source
            self.score = score

    class _FakeRepositoryMapEngine:
        def __init__(self, _repo_root):
            pass

        def search(self, *_args, **_kwargs):
            return [
                _Chunk("rag-helper/out/index_by_kind/python_module_summary.jsonl", 100.0),
                _Chunk("agent/codecompass_context.py", 90.0),
            ]

    captured: dict = {}

    def _fake_tool_loop(**kwargs):
        captured.update(kwargs)
        return "answer", {"tool_calls_made": 0}

    monkeypatch.setattr("agent.hybrid_orchestrator.RepositoryMapEngine", _FakeRepositoryMapEngine)
    monkeypatch.setattr("agent.routes.snakes_rag_tool_loop.run_rag_chat_tool_loop", _fake_tool_loop)

    answer, trace = mod.worker_chat_rag_iterative("erklaere CodeCompass")

    assert answer == "answer"
    assert captured["initial_files"] == ["agent/codecompass_context.py"]
    assert trace["available_files"] == ["agent/codecompass_context.py"]
