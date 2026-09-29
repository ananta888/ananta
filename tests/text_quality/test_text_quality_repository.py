from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models import TextQualityCriteriaSetDB, TextQualityEvaluationDB
from agent.repositories import text_quality as repository_module
from agent.repositories.text_quality import (
    TextQualityCriteriaSetRepository,
    TextQualityEvaluationRepository,
)


def _engine(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repository_module, "engine", engine)
    return engine


def test_criteria_deduplication_and_atomic_active_replacement(monkeypatch):
    _engine(monkeypatch)
    repo = TextQualityCriteriaSetRepository()
    first = repo.save(
        TextQualityCriteriaSetDB(
            id="one",
            version="1",
            language="de",
            profile_name="critical_editor_de",
            content_kinds=["freeform_prose"],
            checksum="a" * 64,
        )
    )
    duplicate = repo.save(
        TextQualityCriteriaSetDB(
            id="duplicate",
            version="1",
            language="de",
            profile_name="critical_editor_de",
            content_kinds=["freeform_prose"],
            checksum="a" * 64,
        )
    )
    second = repo.save(
        TextQualityCriteriaSetDB(
            id="two",
            version="2",
            language="de",
            profile_name="critical_editor_de",
            content_kinds=["freeform_prose"],
            checksum="b" * 64,
        )
    )
    assert duplicate.id == first.id
    repo.set_status(first.id, "enabled")
    repo.set_status(second.id, "enabled")
    assert repo.get_by_id(first.id).status == "archived"
    assert repo.get_active("critical_editor_de", "de", "freeform_prose").id == second.id


def test_evaluation_save_is_idempotent_by_identity(monkeypatch):
    _engine(monkeypatch)
    repo = TextQualityEvaluationRepository()
    fields = {
        "evaluator_version": "v1",
        "criteria_version": "c1",
        "language": "de",
        "content_kind": "freeform_prose",
        "status": "completed",
        "identity_checksum": "identity",
    }
    first = repo.save(TextQualityEvaluationDB(id="eval-one", **fields))
    second = repo.save(TextQualityEvaluationDB(id="eval-two", **fields))
    assert first.id == second.id

