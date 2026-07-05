import json
from pathlib import Path

from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from agent.services.retrieval_source_adapters import OpenNotebookKnowledgeSourceAdapter
from tests.open_notebook_test_fakes import build_importer

FIXTURE = Path(__file__).parent / "fixtures" / "open_notebook" / "complex_export.json"


def test_fixture_import_to_retrieval_is_offline_and_grounded(tmp_path):
    env = build_importer(tmp_path)
    result = env.importer.import_export(json.loads(FIXTURE.read_text(encoding="utf-8")))

    assert result["imported"]["sources"] == 3
    assert result["artifact_ids"]
    assert result["collection_ids"]
    descriptor = env.registry.get_source(result["registry_source_id"])
    assert descriptor["source_type"] == "open_notebook"
    snapshot = env.snapshots.list_snapshots(source_id=result["registry_source_id"])[0]
    assert snapshot["extensions"]["source_system"] == "open_notebook"

    retrieval = KnowledgeIndexRetrievalService(
        knowledge_index_repository=env.index_repo,
        knowledge_link_repository=env.link_repo,
    )
    chunks = OpenNotebookKnowledgeSourceAdapter(retrieval).search(
        "hub orchestration delegation", top_k=4, retrieval_intent="research"
    )
    assert chunks
    assert all(chunk.metadata["source_type"] == "open_notebook" for chunk in chunks)
    assert all(chunk.metadata.get("snapshot_id") for chunk in chunks if chunk.metadata["record_kind"] == "primary_source")
