from types import SimpleNamespace

from agent.services.source_transformation_service import SourceTransformationService
from agent.sources.open_notebook_import_state import OpenNotebookImportStateStore
from tests.open_notebook_test_fakes import FakeArtifactRepo, FakeIngestionService


class _Chat:
    def __init__(self, answer="grounded summary"):
        self.answer_text = answer

    def answer(self, **_kwargs):
        if self.answer_text is None:
            raise ValueError("source_context_unavailable_for_llm_scope")
        return {"answer": self.answer_text}


def _reference():
    return {"source_id": "open-notebook-ref", "snapshot_id": "snap_1234567890abcdef"}


def test_success_and_idempotent_repeat(tmp_path):
    ingestion = FakeIngestionService()
    repo = FakeArtifactRepo()
    service = SourceTransformationService(
        source_chat_service=_Chat(),
        ingestion_service=ingestion,
        artifact_repository=repo,
        state_store=OpenNotebookImportStateStore(root=tmp_path),
    )
    first = service.transform(
        source_reference=_reference(), transformation_id="summary", execution_scope="local_only"
    )
    second = service.transform(
        source_reference=_reference(), transformation_id="summary", execution_scope="local_only"
    )
    assert first["status"] == "completed"
    assert second["reason_code"] == "duplicate_transformation"
    assert len(ingestion.uploads) == 1
    artifact = repo.get_by_id(first["artifact_id"])
    assert artifact.artifact_metadata["record_kind"] == "source_insight"


def test_missing_template_and_empty_context_fail_without_artifact(tmp_path):
    service = SourceTransformationService(
        source_chat_service=_Chat(None),
        ingestion_service=FakeIngestionService(),
        artifact_repository=FakeArtifactRepo(),
        state_store=OpenNotebookImportStateStore(root=tmp_path),
    )
    assert service.transform(
        source_reference=_reference(), transformation_id="missing", execution_scope="local_only"
    )["reason_code"] == "transformation_template_not_found"
    assert service.transform(
        source_reference=_reference(), transformation_id="summary", execution_scope="local_only"
    )["status"] == "failed"
