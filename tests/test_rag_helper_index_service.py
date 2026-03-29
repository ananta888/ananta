import json
from pathlib import Path

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
