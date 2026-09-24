import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from agent.services.knowledge_index_consumption_policy import (
    KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY,
    KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
    KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
)
from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
from ananta_contracts.file_type_support import load_file_type_support_registry


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


def test_id_only_record_keeps_exact_empty_path_locator(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "id": "migrations/organization_schema.py",
                "kind": "source_file",
                "content": "organization source catalog planning research",
            }
        ),
        encoding="utf-8",
    )
    index = SimpleNamespace(
        id="idx-id-only",
        artifact_id=None,
        source_scope="artifact",
        profile_name="default",
        output_dir=str(output_dir),
        status="completed",
    )
    service = KnowledgeIndexRetrievalService(
        knowledge_index_repository=SimpleNamespace(
            list_completed=lambda: [index]
        )
    )

    records = service.search_records(
        "organization source catalog",
        limit=1,
    )

    assert len(records) == 1
    assert records[0]["id"] == "migrations/organization_schema.py"
    assert records[0]["path"] == ""
    assert records[0]["source"] == "migrations/organization_schema.py"
    binding = {
        "source_id": "SRC_ID_ONLY",
        "record_file": "index.jsonl",
        "record_id": records[0]["id"],
        "path": None,
        "line_start": None,
        "line_end": None,
        "content_hash": hashlib.sha256(
            records[0]["content"].encode("utf-8")
        ).hexdigest(),
    }
    assert len(
        service.load_bound_records(
            knowledge_index=index,
            bindings=[binding],
        )
    ) == 1


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


def test_artifact_allow_list_excludes_non_artifact_source_scopes(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "kind": "md_section",
                "file": "src/private.md",
                "content": "private repository timeout handling",
            }
        ),
        encoding="utf-8",
    )
    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-repo-1",
                artifact_id="artifact-1",
                source_scope="registered_workspace",
                profile_name="default",
                output_dir=str(output_dir),
            )
        ]
    )
    service = KnowledgeIndexRetrievalService(
        knowledge_index_repository=repository
    )

    assert service.search(
        "private timeout",
        artifact_ids={"artifact-1"},
    ) == []


def test_v2_non_artifact_retrieval_is_scoped_to_projected_allowed_indices(
    tmp_path,
):
    def _output(name: str, content: str) -> Path:
        output_dir = tmp_path / name
        output_dir.mkdir()
        (output_dir / "index.jsonl").write_text(
            json.dumps(
                {
                    "kind": "md_section",
                    "file": f"src/{name}.md",
                    "content": content,
                }
            ),
            encoding="utf-8",
        )
        return output_dir

    def _index(
        index_id: str,
        output_dir: Path,
        *,
        projection_state: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=index_id,
            artifact_id=None,
            source_scope="registered_workspace",
            profile_name="default",
            status="completed",
            output_dir=str(output_dir),
            index_metadata={
                KNOWLEDGE_INDEX_EXECUTION_BINDING_METADATA_KEY: {
                    "schema": KNOWLEDGE_INDEX_MATERIALIZATION_BINDING_SCHEMA,
                    "projection_state": projection_state,
                    "execution_job_schema": KNOWLEDGE_INDEX_EXECUTION_JOB_SCHEMA,
                    "job_id": "knowledge-index-" + ("1" * 32),
                    "knowledge_index_id": index_id,
                    "authority_binding_digest": "a" * 64,
                    "assignment_id": "assignment-1",
                }
            },
        )

    projected_a = _index(
        "idx-a",
        _output("a", "private timeout alpha"),
        projection_state="projected",
    )
    projected_b = _index(
        "idx-b",
        _output("b", "private timeout beta"),
        projection_state="projected",
    )
    provisional = _index(
        "idx-pending",
        _output("pending", "private timeout provisional"),
        projection_state="pending",
    )
    service = KnowledgeIndexRetrievalService(
        knowledge_index_repository=SimpleNamespace(
            list_completed=lambda: [provisional, projected_b, projected_a]
        )
    )

    assert service.search("private timeout") == []

    chunks = service.search(
        "private timeout",
        allowed_index_ids={"idx-a", "idx-pending"},
    )

    assert chunks
    assert {
        chunk.metadata["knowledge_index_id"] for chunk in chunks
    } == {"idx-a"}


def test_knowledge_index_retrieval_prefers_structured_symbol_hits(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "md_section",
                        "file": "docs/payment.md",
                        "title": "Timeout",
                        "content": "timeout timeout timeout worker timeout",
                    }
                ),
                json.dumps(
                    {
                        "kind": "function_symbol",
                        "file": "src/payment_worker.py",
                        "name": "handle_timeout",
                        "symbols": ["PaymentWorker.handle_timeout"],
                        "content": "Handle worker timeout during invoice processing",
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

    chunks = service.search("handle_timeout payment worker", top_k=2, task_kind="bugfix")

    assert len(chunks) == 2
    assert chunks[0].source == "src/payment_worker.py"
    assert chunks[0].metadata["record_kind"] == "function_symbol"
    assert (
        chunks[0].metadata["retrieval_score_breakdown"]["final_score"]
        > chunks[1].metadata["retrieval_score_breakdown"]["final_score"]
    )


def test_knowledge_index_retrieval_task_kind_architecture_boosts_docs(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "function_symbol",
                        "file": "src/service.py",
                        "name": "service_boundary",
                        "content": "service boundary architecture details",
                    }
                ),
                json.dumps(
                    {
                        "kind": "md_section",
                        "file": "docs/architecture.md",
                        "title": "Service boundaries overview",
                        "content": "Architecture overview for service boundaries and responsibilities.",
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

    chunks = service.search("architecture service boundaries overview", top_k=2, task_kind="architecture")

    assert len(chunks) == 2
    assert chunks[0].source == "docs/architecture.md"


def test_knowledge_index_retrieval_codecompass_explanation_prefers_docs_anchor(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "kind": "python_module",
                        "file": "rag-helper/rag_helper/application/config_profiles.py",
                        "name": "config_profiles",
                        "content": "CodeCompass configuration profile implementation code",
                    }
                ),
                json.dumps(
                    {
                        "kind": "md_section",
                        "file": "docs/system-komponenten.md",
                        "title": "CodeCompass - Was ist CodeCompass?",
                        "content": "CodeCompass ist das RAG-Indexierungs- und Retrieval-System von Ananta.",
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

    chunks = service.search(
        "was ist CodeCompass",
        top_k=2,
        task_kind="research",
        retrieval_intent="code_explanation_with_codecompass",
    )

    assert len(chunks) == 2
    assert chunks[0].source == "docs/system-komponenten.md"
    assert chunks[0].metadata["retrieval_score_breakdown"]["file_multiplier"] == 1.45


def test_knowledge_index_retrieval_penalizes_duplicate_and_generated_records(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "java_type:payment-service",
                        "kind": "java_type",
                        "file": "src/payment_service.py",
                        "name": "PaymentService",
                        "symbols": ["PaymentService.retry_timeout"],
                        "content": "retry timeout worker invoice failure handling",
                        "importance_score": 4.2,
                        "role_labels": ["service"],
                    }
                ),
                json.dumps(
                    {
                        "id": "java_type:payment-dto",
                        "kind": "java_type",
                        "file": "target/generated/PaymentDto.java",
                        "name": "PaymentDto",
                        "content": "retry timeout worker invoice failure handling",
                        "importance_score": 0.6,
                        "generated_code": True,
                        "role_labels": ["dto"],
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "relations.jsonl").write_text(
        json.dumps(
            {
                "kind": "relation",
                "relation": "duplicate_candidate",
                "from": "java_type:payment-dto",
                "to": "java_type:payment-service",
            }
        )
        + "\n",
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

    chunks = service.search("retry timeout worker invoice failure", top_k=2, task_kind="bugfix")

    assert len(chunks) == 2
    assert chunks[0].source == "src/payment_service.py"
    assert chunks[0].metadata["generated_code"] is False
    assert chunks[1].metadata["generated_code"] is True
    assert chunks[1].metadata["duplicate_candidate"] is True


def test_knowledge_index_retrieval_uses_focus_terms_from_specialized_chunks(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "details.jsonl").write_text(
        json.dumps(
            {
                "id": "java_member_chunk:payment-service-1",
                "kind": "java_member_chunk",
                "file": "src/payment_service.py",
                "type_name": "PaymentService",
                "member_names": ["retryTimeout", "loadInvoice", "emitMetric"],
                "focus_terms": ["retryTimeout", "invoice", "metric"],
                "retrieval_focus": "type_member_neighborhood",
                "chunk_granularity": "member_group",
                "summary": "Focused PaymentService member chunk",
                "content": "retry timeout logic around invoice worker flow",
                "importance_score": 3.1,
            }
        )
        + "\n",
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

    chunks = service.search("retryTimeout invoice metric", top_k=1, task_kind="refactor")

    assert len(chunks) == 1
    assert chunks[0].metadata["record_kind"] == "java_member_chunk"
    assert chunks[0].metadata["retrieval_score_breakdown"]["final_score"] > 0


def test_knowledge_index_retrieval_can_filter_by_source_scope(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "kind": "md_section",
                "file": "docs/wiki/payment.md",
                "title": "Payment",
                "content": "wiki payment timeout",
            }
        ),
        encoding="utf-8",
    )

    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-wiki-1",
                artifact_id="artifact-wiki",
                source_scope="wiki",
                profile_name="default",
                output_dir=str(output_dir),
            )
        ]
    )
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=repository)

    assert service.search("payment timeout", source_scopes={"artifact"}) == []
    wiki_chunks = service.search("payment timeout", source_scopes={"wiki"})

    assert len(wiki_chunks) == 1
    assert wiki_chunks[0].metadata["source_type"] == "wiki"


def test_knowledge_index_retrieval_wiki_metadata_preserves_revision_and_import_fields(tmp_path):
    output_dir = tmp_path / "knowledge-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "kind": "wiki_section_chunk",
                "file": "wiki/payment.md",
                "article_title": "Payment retries",
                "section_title": "Timeout handling",
                "wiki_article_id": "payment-retries",
                "language": "en",
                "revision": "r17",
                "import_revision": "snapshot-2026-04-23",
                "import_metadata": {"source_path": "/tmp/wiki.jsonl"},
                "content": "Workers retry payment after timeout.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-wiki-1",
                artifact_id="wiki-mvp",
                source_scope="wiki",
                profile_name="default",
                output_dir=str(output_dir),
            )
        ]
    )
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=repository)

    chunks = service.search("payment timeout", source_scopes={"wiki"})

    assert len(chunks) == 1
    metadata = chunks[0].metadata
    assert metadata["source_type"] == "wiki"
    assert metadata["wiki_article_id"] == "payment-retries"
    assert metadata["revision"] == "r17"
    assert metadata["import_revision"] == "snapshot-2026-04-23"
    citation = metadata.get("citation") or {}
    assert citation.get("revision") == "snapshot-2026-04-23"


def test_file_kind_buckets_follow_canonical_registry_families_and_selectors():
    registry = load_file_type_support_registry(Path(__file__).resolve().parents[1])
    service = KnowledgeIndexRetrievalService(file_type_registry=registry)

    assert {descriptor.family for descriptor in registry.descriptors} <= set(
        service.FILE_KIND_BY_FAMILY
    )
    assert service._file_kind_bucket("Sources/Account.swift") == "code"
    assert service._file_kind_bucket("frontend/UserCard.vue") == "code"
    assert service._file_kind_bucket("scripts/deploy.ps1") == "code"
    assert service._file_kind_bucket("Dockerfile") == "config"
    assert service._file_kind_bucket(".github/workflows/ci.yml") == "config"
    assert service._file_kind_bucket("schemas/order.schema.json") == "config"
    assert service._file_kind_bucket("docs/architecture/system.mmd") == "doc"
    assert service._file_kind_bucket("README.md") == "doc"
    assert service._file_kind_bucket("unknown.custom") == "other"


def _records_index(tmp_path, records):
    output_dir = tmp_path / "records-index"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    repository = SimpleNamespace(
        list_completed=lambda: [
            SimpleNamespace(
                id="idx-records",
                artifact_id=None,
                source_scope="repo_path",
                profile_name="deep_code",
                output_dir=str(output_dir),
            )
        ]
    )
    return KnowledgeIndexRetrievalService(knowledge_index_repository=repository)


def test_records_ingestion_path_is_citable_and_a_repetitive_blob_no_longer_wins(tmp_path):
    """Live regression (MCK-008): "rag-helper" returned a pathless registry blob first.

    Records ingestion keeps the file only in ``metadata.relative_path``; the blob
    repeats the query tokens (and substrings like "rag" in "storage") hundreds
    of times, which the linear count formula used to reward without bound.
    """
    registry = json.dumps({"schema": "codecompass.file-type-support-registry.v1"}) + (
        ' {"parser_strategy": "rag_helper", "storage": "rag-helper helper"}' * 400
    )
    service = _records_index(
        tmp_path,
        [
            {
                "id": "config/codecompass/file_type_support.v1.json",
                "content": registry,
                "metadata": {"file_type": "json", "relative_path": "config/codecompass/file_type_support.v1.json"},
            },
            {
                "id": "agent/services/rag_helper_index_service.py",
                "content": "class RagHelperIndexService: indexes a repository path with the rag-helper profile",
                "metadata": {"file_type": "python", "relative_path": "agent/services/rag_helper_index_service.py"},
            },
            {
                "id": "docs/rag-helper.md",
                "content": "# RAG-Helper\nWhat the rag-helper is good for.",
                "metadata": {"file_type": "md", "relative_path": "docs/rag-helper.md"},
            },
        ],
    )

    records = service.search_records("rag-helper", limit=3)

    display = [record["metadata"]["display_path"] for record in records]
    assert set(display[:2]) == {"agent/services/rag_helper_index_service.py", "docs/rag-helper.md"}
    assert display[2] == "config/codecompass/file_type_support.v1.json"
    # The hydration locator is unchanged: these records carry no top-level path.
    assert all(record["path"] == "" for record in records)
    assert records[0]["metadata"]["retrieval_score_breakdown"]["path_stem_bonus"] > 0


def test_repeated_token_hits_saturate():
    service = KnowledgeIndexRetrievalService(knowledge_index_repository=SimpleNamespace(list_completed=lambda: []))
    capped = service._weighted_token_hits(["rag"], "rag " * 9, 1.0)
    assert capped == service._weighted_token_hits(["rag"], "rag " * 900, 1.0)
    assert service._weighted_token_hits(["rag"], "rag " * 2, 1.0) < capped


def test_underscore_hyphen_and_space_variants_name_the_same_file_stem(tmp_path):
    service = _records_index(
        tmp_path,
        [
            {
                "id": "agent/services/rag_helper_index_service.py",
                "content": "class RagHelperIndexService: indexes a repository path",
                "metadata": {"file_type": "python", "relative_path": "agent/services/rag_helper_index_service.py"},
            },
            {
                "id": ".hermes/plans/e2e-pipeline.md",
                "content": "pipeline stages of the pipeline",
                "metadata": {"file_type": "text", "relative_path": ".hermes/plans/e2e-pipeline.md"},
            },
        ],
    )
    for query in ("rag-helper", "rag_helper", "rag helper", "rag_helper pipeline"):
        first = service.search_records(query, limit=2)[0]
        assert first["metadata"]["display_path"] == "agent/services/rag_helper_index_service.py", query
        assert first["metadata"]["retrieval_score_breakdown"]["path_stem_bonus"] == 5.0, query


def test_tests_follow_the_module_for_explanations_but_not_for_test_tasks(tmp_path):
    content = "RagHelperIndexService rag_helper index service"
    service = _records_index(
        tmp_path,
        [
            {"id": path, "content": content, "metadata": {"file_type": "python", "relative_path": path}}
            for path in ("tests/test_rag_helper_index_service.py", "agent/services/rag_helper_index_service.py")
        ],
    )
    explained = service.search_records("rag_helper", limit=2)
    assert [record["metadata"]["display_path"] for record in explained] == [
        "agent/services/rag_helper_index_service.py",
        "tests/test_rag_helper_index_service.py",
    ]
    assert explained[1]["metadata"]["retrieval_score_breakdown"]["test_penalty"] == 0.2
    tested = service.search_records("rag_helper", limit=2, task_kind="test")
    assert len({record["score"] for record in tested}) == 1
    assert tested[0]["metadata"]["retrieval_score_breakdown"]["test_penalty"] == 0.0
