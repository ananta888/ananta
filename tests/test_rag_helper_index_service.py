import json
from pathlib import Path

from agent.db_models import KnowledgeIndexDB
from agent.repository import knowledge_index_repo
from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService


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
    assert any(item["name"] == "spring-large-project-profile-ultra-backend-java-xml-overview-no-resume" for item in profiles)


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
