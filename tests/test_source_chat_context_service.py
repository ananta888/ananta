import pytest

from agent.services.source_chat_context_service import SourceChatContextService


def _chunk(record_kind: str, *, content: str, source_id: str = "open-notebook-abc123def456", score: float = 1.0):
    return {
        "engine": "knowledge_index",
        "source": f"open-notebook/{record_kind}.md",
        "content": content,
        "score": score,
        "metadata": {
            "source_type": "open_notebook",
            "source_id": source_id,
            "registry_source_id": source_id,
            "open_notebook_source_id": "src-1",
            "snapshot_id": "snap_1234567890abcdef",
            "chunk_id": f"onb:{record_kind}-{len(content)}",
            "artifact_id": "art-1",
            "record_kind": record_kind,
            "source_title": "Survey",
            "content_hash": "hash-1",
        },
    }


class _FakeRagService:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def retrieve_context_bundle(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return {"query": query, "chunks": list(self._chunks), "explainability": {}, "llm_scope": kwargs.get("llm_scope")}


class _FakeRegistry:
    def __init__(self, sources=None):
        self._sources = sources or {"open-notebook-abc123def456": {"source_id": "open-notebook-abc123def456", "enabled": True}}

    def get_source(self, source_id):
        return self._sources.get(source_id)


def _service(chunks, registry=None):
    rag = _FakeRagService(chunks)
    service = SourceChatContextService(
        rag_service=rag,
        source_registry=registry or _FakeRegistry(),
        grounded_prompt_builder=lambda *, prompt, context_text, chunks: f"Q:{prompt}|CTX:{len(chunks)}",
    )
    return service, rag


def test_source_only_context_filters_notes_and_insights():
    chunks = [
        _chunk("primary_source", content="primary content"),
        _chunk("source_insight", content="insight content"),
        _chunk("note", content="note content"),
    ]
    service, rag = _service(chunks)
    result = service.build_context(prompt="question", source_ref="open-notebook-abc123def456", include_insights=False)

    kinds = {item["record_kind"] for item in result["selected_sources"]}
    assert kinds == {"primary_source"}
    assert rag.calls[0]["source_types"] == ["open_notebook"]
    assert rag.calls[0]["source_constraints"] == {"source_ref": "open-notebook-abc123def456"}
    assert result["grounded_prompt"] == "Q:question|CTX:1"
    assert result["context_hash"]
    assert result["source_references"]


def test_insights_are_included_when_requested():
    chunks = [_chunk("primary_source", content="p"), _chunk("source_insight", content="i")]
    service, _rag = _service(chunks)
    result = service.build_context(prompt="q", source_ref="open-notebook-abc123def456", include_insights=True)
    kinds = {item["record_kind"] for item in result["selected_sources"]}
    assert kinds == {"primary_source", "source_insight"}


def test_notes_are_included_only_when_enabled():
    chunks = [_chunk("primary_source", content="p"), _chunk("note", content="n")]
    service, _rag = _service(chunks)
    with_notes = service.build_context(
        prompt="q", source_ref="open-notebook-abc123def456", include_notes=True
    )
    kinds = {item["record_kind"] for item in with_notes["selected_sources"]}
    assert kinds == {"primary_source", "note"}


def test_budget_cut_limits_chunks_and_chars():
    chunks = [_chunk("primary_source", content="x" * 400, score=5.0 - i) for i in range(10)]
    service, _rag = _service(chunks)
    result = service.build_context(
        prompt="q",
        source_ref="open-notebook-abc123def456",
        max_chunks=3,
        max_context_chars=1000,
    )
    assert result["budget"]["used_chunks"] <= 3
    assert result["budget"]["used_chars"] <= 1000
    assert result["budget"]["budget_cut"] is True


def test_missing_source_raises_source_not_found():
    service, _rag = _service([])
    with pytest.raises(ValueError, match="source_not_found"):
        service.build_context(prompt="q", source_ref="unknown-source")


def test_missing_input_raises_source_ref_required():
    service, _rag = _service([])
    with pytest.raises(ValueError, match="source_ref_required"):
        service.build_context(prompt="q")


def test_artifact_and_snapshot_filters_match():
    chunks = [_chunk("primary_source", content="p")]
    service, _rag = _service(chunks)
    by_artifact = service.build_context(prompt="q", artifact_id="art-1")
    assert by_artifact["selected_sources"]
    by_snapshot = service.build_context(prompt="q", snapshot_id="snap_1234567890abcdef")
    assert by_snapshot["selected_sources"]
    miss = service.build_context(prompt="q", artifact_id="art-other")
    assert miss["selected_sources"] == []


def test_context_hash_is_deterministic_over_source_snapshot_content():
    chunks = [_chunk("primary_source", content="p")]
    service_one, _ = _service(chunks)
    service_two, _ = _service(chunks)
    hash_one = service_one.build_context(prompt="q", source_ref="open-notebook-abc123def456")["context_hash"]
    hash_two = service_two.build_context(prompt="q", source_ref="open-notebook-abc123def456")["context_hash"]
    assert hash_one == hash_two
