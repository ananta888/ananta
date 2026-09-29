from __future__ import annotations

from types import SimpleNamespace

from agent.services.wiki_import_job_service import WikiImportJobService


class _FakeIngestion:
    def import_wiki_corpus(self, **_kwargs):
        return {
            "source_id": "wiki-fake",
            "records": [{"kind": "wiki_section_chunk", "chunk_id": "wiki:c1", "content": "x"}],
            "issues": [],
            "stats": {"pages": 1},
        }


class _FakeIndex:
    def index_source_records(self, **_kwargs):
        index = SimpleNamespace(model_dump=lambda: {"id": "idx-1", "source_scope": "wiki"})
        run = SimpleNamespace(status="completed", model_dump=lambda: {"id": "run-1", "status": "completed"})
        return index, run


def test_wiki_import_job_service_pause_resume_cancel_state_machine():
    service = WikiImportJobService(ingestion_service=_FakeIngestion(), index_service=_FakeIndex(), max_workers=1)
    job = service.submit_import_job(
        import_request={"corpus_path": "/tmp/a.xml", "source_id": "wiki-fake", "language": "de"},
        from_url=False,
        created_by="tester",
    )
    job_id = str(job["job_id"])
    paused = service.pause_job(job_id)
    assert paused is not None
    resumed = service.resume_job(job_id)
    assert resumed is not None
    cancelled = service.cancel_job(job_id)
    assert cancelled is not None
    assert cancelled["cancel_requested"] is True
