from types import SimpleNamespace

from agent.sources.open_notebook_insights_importer import OpenNotebookInsightsImporter
from tests.open_notebook_test_fakes import FakeArtifactRepo, FakeIngestionService


def _importer():
    ingestion = FakeIngestionService()
    artifact_repo = FakeArtifactRepo()
    importer = OpenNotebookInsightsImporter(ingestion_service=ingestion, artifact_repository=artifact_repo)
    return importer, ingestion, artifact_repo


def _parents():
    snapshot = {"snapshot_id": "snap_1234567890abcdef"}
    artifact = SimpleNamespace(id="art-parent-1")
    return {"src-1": snapshot}, {"src-1": artifact}


def _import(importer, insights, snapshots=None, artifacts=None):
    default_snapshots, default_artifacts = _parents()
    return importer.import_insights(
        insights,
        import_key="key-1",
        registry_source_id="open-notebook-abc",
        snapshots_by_source=snapshots if snapshots is not None else default_snapshots,
        artifacts_by_source=artifacts if artifacts is not None else default_artifacts,
        created_by="tester",
    )


def test_insight_with_valid_parent_is_imported_as_derived_artifact():
    importer, _ingestion, artifact_repo = _importer()
    result = _import(
        importer,
        [
            {
                "id": "insight-1",
                "source_id": "src-1",
                "insight_type": "key_terms",
                "transformation_name": "Key Terms",
                "content": "planning, memory, delegation",
            }
        ],
    )
    assert result["imported"] == 1
    artifact = artifact_repo.saved[result["artifact_ids"][0]]
    metadata = artifact.artifact_metadata
    assert metadata["record_kind"] == "source_insight"
    assert metadata["derived_from"] == "open_notebook"
    assert metadata["transformation_name"] == "Key Terms"
    assert metadata["parent_source_ref"]["open_notebook_source_id"] == "src-1"
    assert metadata["parent_source_ref"]["artifact_id"] == "art-parent-1"
    assert metadata["parent_source_ref"]["snapshot_id"] == "snap_1234567890abcdef"

    record = result["records"][0]
    assert record["kind"] == "open_notebook_insight_chunk"
    meta = record["import_metadata"]
    assert meta["record_kind"] == "source_insight"
    assert meta["derived_from"] == "open_notebook"
    assert meta["parent_source_id"] == "src-1"
    assert meta["parent_source_snapshot_id"] == "snap_1234567890abcdef"
    assert meta["transformation_name"] == "Key Terms"
    assert meta["insight_type"] == "key_terms"


def test_insight_without_parent_source_is_skipped_with_warning():
    importer, ingestion, _repo = _importer()
    result = _import(
        importer,
        [{"id": "insight-orphan", "source_id": "src-missing", "content": "text"}],
    )
    assert result["imported"] == 0
    assert result["skipped"] == 1
    issue = result["issues"][0]
    assert issue["reason_code"] == "insight_missing_parent_source"
    assert issue["parent_source_id"] == "src-missing"
    assert ingestion.uploads == []


def test_insights_are_not_deduplicated_against_parent_source():
    importer, _ingestion, _repo = _importer()
    # insight content identical to hypothetical parent content still imports
    result = _import(
        importer,
        [
            {"id": "insight-a", "source_id": "src-1", "content": "identical text"},
            {"id": "insight-b", "source_id": "src-1", "content": "identical text"},
        ],
    )
    assert result["imported"] == 2
    chunk_ids = [record["chunk_id"] for record in result["records"]]
    assert len(chunk_ids) == len(set(chunk_ids))
