import json
from pathlib import Path

from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from agent.services.retrieval_source_adapters import OpenNotebookKnowledgeSourceAdapter
from agent.services.retrieval_source_contract import enabled_source_types_from_settings
from tests.open_notebook_test_fakes import build_importer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "open_notebook"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _adapter_from_import(tmp_path, fixture="complex_export.json"):
    env = build_importer(tmp_path)
    result = env.importer.import_export(_load(fixture))
    knowledge_service = KnowledgeIndexRetrievalService(knowledge_index_repository=env.index_repo)
    return OpenNotebookKnowledgeSourceAdapter(knowledge_service), result


def test_adapter_returns_hits_with_complete_metadata(tmp_path):
    adapter, result = _adapter_from_import(tmp_path)
    chunks = adapter.search("hub-centric orchestration delegation", top_k=4)
    assert chunks
    chunk = chunks[0]
    assert chunk.engine == "knowledge_index"
    metadata = chunk.metadata
    assert metadata["source_type"] == "open_notebook"
    assert metadata["source_system"] in {"open_notebook", "open_notebook_note"}
    assert metadata["source_id"] == result["registry_source_id"]
    assert metadata["record_kind"] in {"primary_source", "note", "source_insight"}
    assert metadata["artifact_id"]
    assert metadata["chunk_id"]
    # primary sources carry their snapshot id
    primary = [c for c in chunks if c.metadata.get("record_kind") == "primary_source"]
    assert primary
    assert primary[0].metadata["snapshot_id"].startswith("snap_")
    # normalize_chunk_metadata ran: citation and security metadata present
    assert metadata["citation"]["source_type"] == "open_notebook"
    assert metadata["security_metadata"]["source_origin"] == "open_notebook"


def test_adapter_empty_search_returns_no_chunks(tmp_path):
    adapter, _result = _adapter_from_import(tmp_path)
    assert adapter.search("xylophon quantenkaskade blubberwort", top_k=4) == []


def test_adapter_ignores_chat_session_records(tmp_path):
    env = build_importer(tmp_path)
    env.importer.import_export(_load("complex_export.json"))
    # inject a rogue chat record into the index to prove the adapter filters it
    index_file = Path(env.index_repo.saved[0].output_dir) / "index.jsonl"
    rogue = {
        "kind": "open_notebook_chat_chunk",
        "id": "chat-rogue:1",
        "chunk_id": "onb-chat:deadbeef",
        "file": "open-notebook/chat.md",
        "title": "chat",
        "content": "hub-centric orchestration chat answer",
        "import_metadata": {"source_scope": "open_notebook", "record_kind": "chat_session"},
    }
    index_file.write_text(index_file.read_text(encoding="utf-8") + json.dumps(rogue) + "\n", encoding="utf-8")
    knowledge_service = KnowledgeIndexRetrievalService(knowledge_index_repository=env.index_repo)
    adapter = OpenNotebookKnowledgeSourceAdapter(knowledge_service)
    chunks = adapter.search("hub-centric orchestration", top_k=8)
    assert chunks
    assert all(chunk.metadata.get("record_kind") != "chat_session" for chunk in chunks)


def test_adapter_downweights_notes_against_primary_sources(tmp_path):
    from types import SimpleNamespace

    content = "identical retrieval budget statement for weighting"
    output_dir = tmp_path / "controlled-index"
    output_dir.mkdir()
    records = [
        {
            "kind": "open_notebook_source_chunk",
            "id": "src:1",
            "chunk_id": "onb:src1",
            "file": "open-notebook/src.md",
            "title": "budget",
            "content": content,
            "import_metadata": {"source_scope": "open_notebook", "record_kind": "primary_source"},
        },
        {
            "kind": "open_notebook_note_chunk",
            "id": "note:1",
            "chunk_id": "onb-note:note1",
            "file": "open-notebook/notes/note.md",
            "title": "budget",
            "content": content,
            "import_metadata": {"source_scope": "open_notebook", "record_kind": "note", "note_type": "human"},
        },
    ]
    (output_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )
    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-controlled",
                artifact_id=None,
                source_scope="open_notebook",
                profile_name="open_notebook_import",
                output_dir=str(output_dir),
            )
        ]
    )
    adapter = OpenNotebookKnowledgeSourceAdapter(
        KnowledgeIndexRetrievalService(knowledge_index_repository=repository)
    )
    chunks = adapter.search("identical retrieval budget statement", top_k=4)
    by_kind = {chunk.metadata["record_kind"]: chunk for chunk in chunks}
    assert set(by_kind) == {"primary_source", "note"}
    assert by_kind["primary_source"].score > by_kind["note"].score


def test_open_notebook_source_is_disabled_by_default():
    from agent.config import settings

    enabled = enabled_source_types_from_settings(settings)
    assert "open_notebook" not in enabled


def test_open_notebook_source_can_be_enabled(monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "rag_source_open_notebook_enabled", True)
    enabled = enabled_source_types_from_settings(settings)
    assert "open_notebook" in enabled
