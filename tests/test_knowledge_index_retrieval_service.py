import json
from types import SimpleNamespace

from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService


def test_knowledge_index_retrieval_service_reads_completed_outputs(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "md_section",
                        "file": "docs/payment-timeouts.md",
                        "title": "Timeout handling",
                        "content": "The worker retries invoice processing after a timeout.",
                    }
                ),
                json.dumps(
                    {
                        "kind": "md_section",
                        "file": "docs/other.md",
                        "title": "Unrelated",
                        "content": "This section is not relevant.",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-1",
                artifact_id="artifact-1",
                source_scope="artifact",
                profile_name="default",
                output_dir=str(output_dir),
            )
        ]
    )
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=repository)

    chunks = service.search("invoice timeout worker", top_k=3)

    assert chunks
    assert chunks[0].engine == "knowledge_index"
    assert chunks[0].source == "docs/payment-timeouts.md"
    assert "timeout" in chunks[0].content.lower()
    assert chunks[0].metadata["knowledge_index_id"] == "idx-1"
    assert chunks[0].metadata["record_kind"] == "md_section"


def test_knowledge_index_retrieval_service_can_filter_by_artifact_id(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "kind": "md_section",
                "file": "docs/payment-timeouts.md",
                "title": "Timeout handling",
                "content": "The worker retries invoice processing after a timeout.",
            }
        ),
        encoding="utf-8",
    )

    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-1",
                artifact_id="artifact-1",
                source_scope="artifact",
                profile_name="default",
                output_dir=str(output_dir),
            )
        ]
    )
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=repository)

    assert service.search("timeout", artifact_ids={"artifact-2"}) == []
    assert service.search("timeout", artifact_ids={"artifact-1"})
