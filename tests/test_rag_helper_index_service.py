import json
from pathlib import Path

import pytest

from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.repository import knowledge_index_repo
from agent.services import rag_helper_index_service as rag_helper_index_module
from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService
from worker.retrieval.knowledge_index_job_handler import (
    BOUND_JOB_SCHEMA,
    RagHelperKnowledgeIndexExecution,
)


def test_rag_helper_index_service_runs_against_markdown_artifact():
    ingestion = IngestionService()
    artifact, _version, _collection = ingestion.upload_artifact(
        filename="README.md",
        content=b"# Payment Timeout\n\nThe worker retries invoice processing after a timeout.\n",
        created_by="tester",
        media_type="text/markdown",
    )

    knowledge_index, run = RagHelperIndexService().index_artifact(artifact.id, created_by="tester")

    assert knowledge_index.status == "completed"
    assert run.status == "completed"
    assert run.output_dir

    output_dir = Path(run.output_dir)
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    index_rows = [
        json.loads(line)
        for line in (output_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert manifest["file_count"] == 1
    assert manifest["index_record_count"] >= 1
    assert any("payment timeout" in json.dumps(row).lower() for row in index_rows)


def test_rag_helper_index_service_exposes_profile_catalog():
    profiles = RagHelperIndexService().list_profiles()

    assert profiles
    assert any(item["name"] == "default" and item["is_default"] for item in profiles)
    assert any(item["name"] == "deep_code" for item in profiles)
    assert any(item["name"] == "subtask_bugfix_local" for item in profiles)
    assert any(item["name"] == "subtask_architecture_review" for item in profiles)
    assert any(item["name"] == "spring-large-project-profile-ultra-backend-java-xml-overview-no-resume" for item in profiles)


def test_rag_helper_index_service_can_suggest_subtask_profile():
    service = RagHelperIndexService()

    assert (
        service.suggest_profile_name(
            task_kind="bugfix",
            retrieval_intent="localize_failure_and_fix",
        )
        == "subtask_bugfix_local"
    )
    assert (
        service.suggest_profile_name(
            task_kind="architecture",
            retrieval_intent="architecture_and_decision_context",
        )
        == "subtask_architecture_review"
    )


def test_rag_helper_index_service_supports_external_xml_overview_profiles():
    ingestion = IngestionService()
    artifact, _version, _collection = ingestion.upload_artifact(
        filename="beans.xml",
        content=b"<beans><bean id='paymentService'/><bean id='retryPolicy'/></beans>",
        created_by="tester",
        media_type="application/xml",
    )

    service = RagHelperIndexService()
    knowledge_index, run = service.index_artifact(
        artifact.id,
        created_by="tester",
        profile_name="spring-large-project-profile-ultra-backend-java-xml-overview-no-resume",
    )

    assert knowledge_index.status == "completed"
    assert run.status == "completed"

    preview = service.get_artifact_preview(artifact.id, limit=3)

    assert preview is not None
    assert preview["preview"]["xml_overview"]
    assert preview["preview"]["xml_overview"][0]["kind"] == "xml_overview"
    assert preview["manifest"]["partitioned_outputs"]["xml_overview"] == ["xml_overview.jsonl"]


def test_rag_helper_index_service_exposes_gems_partition_previews(tmp_path):
    ingestion = IngestionService()
    artifact, _version, _collection = ingestion.upload_artifact(
        filename="README.md",
        content=b"# Payments\n\nWorker owns retries and billing.\n",
        created_by="tester",
        media_type="text/markdown",
    )

    output_dir = tmp_path / "rag-output"
    gems_dir = output_dir / "gems_by_domain"
    gems_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "partitioned_outputs": {
                    "gems": [
                        "gems_by_domain/architecture.jsonl",
                        "gems_by_domain/configuration.jsonl",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (gems_dir / "architecture.jsonl").write_text(
        json.dumps({"kind": "gem", "domain": "architecture", "title": "Hub owns orchestration"}) + "\n",
        encoding="utf-8",
    )
    (gems_dir / "configuration.jsonl").write_text(
        json.dumps({"kind": "gem", "domain": "configuration", "title": "Retry policy"}) + "\n",
        encoding="utf-8",
    )

    knowledge_index_repo.save(
        KnowledgeIndexDB(
            artifact_id=artifact.id,
            source_scope="artifact",
            profile_name="default",
            status="completed",
            output_dir=str(output_dir),
            manifest_path=str(output_dir / "manifest.json"),
            created_by="tester",
        )
    )

    preview = RagHelperIndexService().get_artifact_preview(artifact.id, limit=3)

    assert preview is not None
    assert preview["available_outputs"]["gems"] == [
        "gems_by_domain/architecture.jsonl",
        "gems_by_domain/configuration.jsonl",
    ]
    assert preview["preview"]["gems_by_domain"]["architecture"][0]["domain"] == "architecture"
    assert preview["preview"]["gems_by_domain"]["configuration"][0]["title"] == "Retry policy"


def test_rag_helper_index_service_indexes_source_records_in_scope_layout():
    service = RagHelperIndexService()
    records = [
        {
            "kind": "wiki_section",
            "file": "wiki/payment.md",
            "article_title": "Payment retries",
            "section_title": "Timeout",
            "content": "Workers retry payment after timeout.",
        },
        {
            "kind": "wiki_section",
            "file": "wiki/payment.md",
            "article_title": "Payment retries",
            "section_title": "Backoff",
            "content": "Use bounded exponential backoff.",
        },
    ]

    knowledge_index, run = service.index_source_records(
        source_scope="wiki",
        source_id="wiki-mvp-corpus",
        records=list(reversed(records)),
        created_by="tester",
    )

    assert knowledge_index.status == "completed"
    assert knowledge_index.source_scope == "wiki"
    assert run.status == "completed"
    assert "/knowledge_indices/wiki/" in str(run.output_dir).replace("\\", "/")
    output_dir = Path(str(run.output_dir))
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_scope"] == "wiki"
    assert manifest["index_record_count"] == 2
    assert manifest["chunking"]["strategy"] == "wiki_sentence_chunks"
    assert manifest["chunking"]["input_record_count"] == 2
    assert manifest["chunking"]["normalized_record_count"] == 2
    lines = [line for line in (output_dir / "index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == sorted(lines)
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["kind"] == "wiki_section_chunk"
    assert parsed[0]["chunk_id"].startswith("wiki:")


def test_source_records_can_build_worker_outputs_without_hub_projection_persistence(
    tmp_path,
    monkeypatch,
):
    class ForbiddenControlPlaneRepository:
        def __getattr__(self, name):
            def fail(*_args, **_kwargs):
                pytest.fail(f"worker called control-plane repository: {name}")

            return fail

    monkeypatch.setattr(
        rag_helper_index_module,
        "knowledge_index_repo",
        ForbiddenControlPlaneRepository(),
    )
    monkeypatch.setattr(
        rag_helper_index_module,
        "knowledge_index_run_repo",
        ForbiddenControlPlaneRepository(),
    )
    service = RagHelperIndexService()
    service._knowledge_output_root = (
        lambda *, source_scope: tmp_path / "worker" / source_scope
    )

    knowledge_index, run = service.index_source_records(
        source_scope="repo_path",
        source_id="delegated-source-revision",
        records=[
            {
                "id": "src/example.py",
                "content": "def example():\n    return True\n",
                "metadata": {"relative_path": "src/example.py"},
            }
        ],
        created_by="hub",
        persist_control_plane_records=False,
    )

    assert knowledge_index.status == "completed"
    assert run.status == "completed"
    assert run.knowledge_index_id == knowledge_index.id
    assert knowledge_index.latest_run_id == run.id
    output_dir = Path(str(run.output_dir))
    assert (output_dir / "manifest.json").is_file()
    assert (output_dir / "index.jsonl").is_file()


def test_source_records_persist_hub_projection_by_default(tmp_path, monkeypatch):
    class TrackingIndexRepository:
        def __init__(self):
            self.get_calls = []
            self.saved = []

        def get_by_scope(self, *, source_scope, scope_id):
            self.get_calls.append((source_scope, scope_id))
            return None

        def save(self, value):
            self.saved.append(value)
            return value

    class TrackingRunRepository:
        def __init__(self):
            self.saved = []

        def save(self, value):
            self.saved.append(value)
            return value

    index_repository = TrackingIndexRepository()
    run_repository = TrackingRunRepository()
    monkeypatch.setattr(
        rag_helper_index_module,
        "knowledge_index_repo",
        index_repository,
    )
    monkeypatch.setattr(
        rag_helper_index_module,
        "knowledge_index_run_repo",
        run_repository,
    )
    service = RagHelperIndexService()
    service._knowledge_output_root = (
        lambda *, source_scope: tmp_path / "hub" / source_scope
    )

    knowledge_index, run = service.index_source_records(
        source_scope="repo_path",
        source_id="hub-owned-source",
        records=[{"id": "README.md", "content": "Hub-owned index"}],
        created_by="hub",
    )

    assert knowledge_index.status == "completed"
    assert run.status == "completed"
    assert index_repository.get_calls == [
        ("repo_path", "hub-owned-source")
    ]
    assert len(index_repository.saved) == 3
    assert len(run_repository.saved) == 2


def test_bound_v2_source_records_disable_worker_projection_persistence():
    captured = {}

    class IndexService:
        def index_source_records(self, **kwargs):
            captured.update(kwargs)
            return (
                KnowledgeIndexDB(id="worker-index", status="completed"),
                KnowledgeIndexRunDB(
                    id="worker-run",
                    knowledge_index_id="worker-index",
                    status="completed",
                ),
            )

    class Publisher:
        def publish(self, **_kwargs):
            return [{"role": "manifest"}, {"role": "index"}]

    result = RagHelperKnowledgeIndexExecution(
        IndexService(),
        artifact_publisher=Publisher(),
    ).execute(
        {
            "schema": BOUND_JOB_SCHEMA,
            "job_id": "bound-job",
            "job_type": "source_records",
            "created_by": "hub",
            "authority_binding": {
                "source_revision_id": "srev-test",
                "source_revision_digest": "a" * 64,
            },
            "payload": {
                "source_scope": "repo_path",
                "source_id": "revision-1",
                "records": [{"id": "README.md", "content": "bound"}],
            },
        }
    )

    assert result["status"] == "completed"
    assert captured["persist_control_plane_records"] is False


def test_legacy_source_records_do_not_persist_hub_projections():
    captured = {}

    class LegacyIndexService:
        def index_source_records(self, **kwargs):
            captured.update(kwargs)
            return (
                KnowledgeIndexDB(id="legacy-index", status="completed"),
                KnowledgeIndexRunDB(
                    id="legacy-run",
                    knowledge_index_id="legacy-index",
                    status="completed",
                ),
            )

    class Publisher:
        def publish(self, **_kwargs):
            return [{"role": "manifest"}, {"role": "index"}]

    result = RagHelperKnowledgeIndexExecution(
        LegacyIndexService(),
        artifact_publisher=Publisher(),
    ).execute(
        {
            "schema": "ananta.knowledge_index_job.v1",
            "job_id": "legacy-job",
            "job_type": "source_records",
            "created_by": "hub",
            "payload": {
                "source_scope": "repo_path",
                "source_id": "legacy-source",
                "records": [{"id": "README.md", "content": "legacy"}],
            },
        }
    )

    assert result["status"] == "completed"
    assert captured["persist_control_plane_records"] is False


def test_repo_source_records_build_deterministic_deep_code_graph(tmp_path):
    service = RagHelperIndexService()
    service._knowledge_output_root = (
        lambda *, source_scope: tmp_path / "knowledge_indices" / source_scope
    )
    records = [
        {
            "id": "src/payments.py",
            "content": "class PaymentService:\n    def retry(self):\n        return True\n",
            "metadata": {
                "relative_path": "src/payments.py",
                "file_type": "python",
            },
        },
        {
            "id": "src/card.ts",
            "content": "export class PaymentCard {}\n",
            "metadata": {
                "relative_path": "src/card.ts",
                "file_type": "typescript",
            },
        },
    ]

    _first_index, first_run = service.index_source_records(
        source_scope="repo_path",
        source_id="source-control-deep-code-graph",
        records=records,
        created_by="tester",
        profile_name="deep_code",
    )
    _second_index, second_run = service.index_source_records(
        source_scope="repo_path",
        source_id="source-control-deep-code-graph",
        records=list(reversed(records)),
        created_by="tester",
        profile_name="deep_code",
    )

    first_output = Path(str(first_run.output_dir))
    second_output = Path(str(second_run.output_dir))
    manifest = json.loads((first_output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["file_count"] == 2
    assert manifest["detail_record_count"] >= 2
    assert manifest["relation_record_count"] >= 2
    assert manifest["chunking"]["strategy"] == "identity+codecompass_graph"
    assert manifest["semantic_budget"]["max_records_per_partition"] > 0
    assert manifest["semantic_budget"]["truncated"] is False
    for filename in (
        "graph_nodes.jsonl",
        "graph_edges.jsonl",
        "semantic_nodes.jsonl",
        "semantic_edges.jsonl",
    ):
        assert (first_output / filename).read_bytes() == (
            second_output / filename
        ).read_bytes()


def test_rag_helper_index_service_wiki_chunk_ids_are_stable_across_rebuilds():
    service = RagHelperIndexService()
    records = [
        {
            "kind": "wiki_section",
            "file": "wiki/payment.md",
            "article_title": "Payment retries",
            "section_title": "Timeout",
            "language": "en",
            "content": "Workers retry payment after timeout. Use bounded backoff.",
        }
    ]

    first_index, first_run = service.index_source_records(
        source_scope="wiki",
        source_id="wiki-stable",
        records=records,
        created_by="tester",
    )
    second_index, second_run = service.index_source_records(
        source_scope="wiki",
        source_id="wiki-stable",
        records=list(reversed(records)),
        created_by="tester",
    )

    first_lines = (Path(str(first_run.output_dir)) / "index.jsonl").read_text(encoding="utf-8").splitlines()
    second_lines = (Path(str(second_run.output_dir)) / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert first_index.id == second_index.id
    assert first_lines == second_lines


def test_index_repo_path_indexes_a_directory(tmp_path):
    src = tmp_path / "mylib"
    src.mkdir()
    (src / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    rag_helper_root = Path(__file__).resolve().parents[1] / "rag-helper"

    service = RagHelperIndexService()
    service._repo_root = lambda: tmp_path
    service._rag_helper_root = lambda: rag_helper_root

    knowledge_index, run = service.index_repo_path("mylib", created_by="tester")

    assert knowledge_index.status == "completed"
    assert run.status == "completed"
    assert knowledge_index.source_scope == "repo_path"
    output_dir = Path(run.output_dir)
    index_rows = [
        json.loads(line)
        for line in (output_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any("add" in json.dumps(row).lower() for row in index_rows)


def test_index_repo_path_skips_if_already_completed(tmp_path):
    src = tmp_path / "skip_lib"
    src.mkdir()
    (src / "utils.py").write_text("def noop(): pass\n", encoding="utf-8")
    rag_helper_root = Path(__file__).resolve().parents[1] / "rag-helper"

    service = RagHelperIndexService()
    service._repo_root = lambda: tmp_path
    service._rag_helper_root = lambda: rag_helper_root

    ki1, run1 = service.index_repo_path("skip_lib", created_by="tester")
    assert ki1.status == "completed"

    ki2, run2 = service.index_repo_path("skip_lib", created_by="tester")
    assert run2.status == "skipped"
    assert ki2.id == ki1.id
    previous_fingerprint = ki2.index_metadata["source_fingerprint"]

    (src / "utils.py").write_text("def noop():\n    return 1\n", encoding="utf-8")
    ki3, run3 = service.index_repo_path("skip_lib", created_by="tester")

    assert run3.status == "completed"
    assert ki3.index_metadata["source_fingerprint"] != previous_fingerprint


def test_index_repo_path_rejects_outside_repo(tmp_path):
    service = RagHelperIndexService()
    service._repo_root = lambda: tmp_path

    import pytest
    with pytest.raises(ValueError, match="path_outside_repo"):
        service.index_repo_path("/etc/passwd", created_by="tester")
