from agent.sources.open_notebook_import_policy import OpenNotebookImportPolicy
from agent.sources.open_notebook_notes_importer import OpenNotebookNotesImporter
from tests.open_notebook_test_fakes import FakeArtifactRepo, FakeIngestionService


def _importer(policy=None):
    ingestion = FakeIngestionService()
    artifact_repo = FakeArtifactRepo()
    importer = OpenNotebookNotesImporter(
        ingestion_service=ingestion,
        artifact_repository=artifact_repo,
        policy=policy,
    )
    return importer, ingestion, artifact_repo


def _import(importer, notes):
    return importer.import_notes(
        notes,
        import_key="key-1",
        registry_source_id="open-notebook-abc",
        collection_names_by_notebook={"nb-1": "Research"},
        created_by="tester",
    )


def test_human_note_is_imported_with_note_metadata():
    importer, ingestion, artifact_repo = _importer()
    result = _import(
        importer,
        [
            {
                "id": "note-1",
                "title": "Check budgets",
                "content": "Compare chunk budgets before enabling reranking.",
                "note_type": "human",
                "notebook_id": "nb-1",
                "source_id": "src-1",
            }
        ],
    )
    assert result["imported"] == 1
    assert result["failed"] == 0
    artifact = artifact_repo.saved[result["artifact_ids"][0]]
    metadata = artifact.artifact_metadata
    assert metadata["source_system"] == "open_notebook_note"
    assert metadata["record_kind"] == "note"
    assert metadata["note_type"] == "human"
    assert metadata["open_notebook"]["note_id"] == "note-1"
    assert ingestion.uploads[0]["collection_name"] == "Research"

    record = result["records"][0]
    assert record["kind"] == "open_notebook_note_chunk"
    assert record["import_metadata"]["record_kind"] == "note"
    assert record["import_metadata"]["retrieval_priority"] == "low"
    assert record["import_metadata"]["note_type"] == "human"


def test_ai_note_and_unknown_note_type_fallback():
    importer, _ingestion, _repo = _importer()
    result = _import(
        importer,
        [
            {"id": "note-ai", "content": "AI summary.", "note_type": "ai", "notebook_id": "nb-1"},
            {"id": "note-x", "content": "No type given.", "notebook_id": "nb-1"},
            {"id": "note-weird", "content": "Weird type.", "note_type": "robot", "notebook_id": "nb-1"},
        ],
    )
    assert result["imported"] == 3
    types = [record["import_metadata"]["note_type"] for record in result["records"]]
    assert types == ["ai", "unknown", "unknown"]


def test_empty_note_content_is_skipped():
    importer, _ingestion, _repo = _importer()
    result = _import(importer, [{"id": "note-empty", "content": "   ", "notebook_id": "nb-1"}])
    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert result["issues"][0]["reason_code"] == "note_empty_content"


def test_disabled_notes_policy_skips_all():
    importer, ingestion, _repo = _importer(policy=OpenNotebookImportPolicy(allow_notes=False))
    result = _import(importer, [{"id": "note-1", "content": "text", "notebook_id": "nb-1"}])
    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert result["issues"][0]["reason_code"] == "notes_import_disabled"
    assert ingestion.uploads == []


def test_note_with_secret_value_is_skipped():
    importer, _ingestion, _repo = _importer()
    result = _import(
        importer,
        [{"id": "note-secret", "content": "token sk-abcdef1234567890", "notebook_id": "nb-1"}],
    )
    assert result["imported"] == 0
    assert result["issues"][0]["reason_code"] == "secret_like_value_blocked"
