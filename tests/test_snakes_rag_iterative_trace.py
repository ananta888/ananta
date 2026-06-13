from __future__ import annotations

from types import SimpleNamespace


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

    class _FakeRagService:
        def build_execution_context(self, *_args, **_kwargs):
            return {
                "chunks": [
                    {
                        "source": "docs/system-komponenten.md",
                        "metadata": {"file_path": "docs/system-komponenten.md"},
                    }
                ]
            }, ""

    from agent.routes import snakes_rag_iterative as mod

    monkeypatch.setattr(mod, "_current_config", lambda: {"chat_full_scan_chars_per_file": 20_000})
    monkeypatch.setattr(mod._cfg_settings, "rag_repo_root", str(tmp_path), raising=False)
    monkeypatch.setattr(mod, "lookup_model_context_tokens", lambda _model: 16_000)
    monkeypatch.setattr("agent.services.rag_service.get_rag_service", lambda: _FakeRagService())
    monkeypatch.setattr(
        "agent.services.retrieval_profile_service.resolve_profile",
        lambda *_args, **_kwargs: SimpleNamespace(
            retrieval_intent="code_explanation_with_codecompass",
            source_types=["artifact", "repo"],
            as_dict=lambda: {},
        ),
    )
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
