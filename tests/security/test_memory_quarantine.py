"""Actual SQL quarantine, parent-summary visibility and scoped policy checks."""

import pytest
from sqlmodel import Session, select

import agent.database as database
from agent.db_models import MemoryQuarantineEventDB
from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_quarantine_service import MemoryQuarantineService
from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from tests.security.memory_fixtures import synthetic_memory_access
from tests.test_memory_tree_store_service import store as store  # noqa: F401


def seed(store):
    chunk, _ = store.ingest_chunk(source_id="source", source_type="doc", label="leaf", content="private canary")
    store.update_lifecycle(chunk.id, "sealed")
    store.upsert_node(node_type="source", label="source:source", summary="derived private canary",
                      child_chunk_ids=[chunk.id])
    return chunk


def events():
    with Session(database.engine) as session:
        return list(session.exec(select(MemoryQuarantineEventDB)).all())


def test_quarantine_hides_chunk_and_summary_and_release_is_explicit(store):  # noqa: F811
    chunk = seed(store)
    service = MemoryQuarantineService(store)
    assert service.quarantine(chunk.id, reason_code="suspected_injection")
    assert service.quarantine(chunk.id, reason_code="suspected_injection")
    assert len(events()) == 1
    assert store.get_chunks_by_source("source") == []
    assert store.get_chunks_by_source("source", lifecycle="quarantined") == []
    assert store.get_node("source", "source:source") is None
    assert store.update_lifecycle(chunk.id, "sealed") is False
    with pytest.raises(MemoryAccessError, match="parent_unavailable"):
        store.upsert_node(node_type="source", label="another", summary="unsafe", child_chunk_ids=[chunk.id])
    assert service.release(chunk.id, reason_code="review_cleared")
    assert store.get_chunks_by_source("source")[0].lifecycle == "sealed"
    assert store.get_node("source", "source:source").summary == "derived private canary"
    assert len(events()) == 2
    assert all("private canary" not in str(event.model_dump()) for event in events())


def test_revoked_parent_cannot_be_released_or_promoted(store):  # noqa: F811
    chunk = seed(store)
    service = MemoryQuarantineService(store)
    assert service.revoke(chunk.id, reason_code="confirmed_compromise")
    assert service.revoke(chunk.id, reason_code="confirmed_compromise")
    assert service.release(chunk.id, reason_code="review_cleared") is False
    assert store.update_lifecycle(chunk.id, "admitted") is False
    assert store.get_node("source", "source:source") is None
    assert len(events()) == 1


def test_foreign_namespace_and_missing_update_grant_cannot_change_state(store):  # noqa: F811
    chunk = seed(store)
    service = MemoryQuarantineService(store)
    with synthetic_memory_access(task_id="foreign", channels=("hub-memory-tree",), scopes=(MemoryScope.task,)):
        assert service.quarantine(chunk.id, reason_code="suspected_injection") is False
    with synthetic_memory_access(channels=("hub-memory-tree",), scopes=(MemoryScope.task,),
                                 operations=(MemoryOperation.read,)):
        with pytest.raises(MemoryAccessError):
            service.quarantine(chunk.id, reason_code="suspected_injection")
    assert len(events()) == 0
    assert store.get_node("source", "source:source") is not None


def test_policy_revoked_before_commit_rolls_back_state_and_event(store, monkeypatch):  # noqa: F811
    chunk = seed(store)
    service = MemoryQuarantineService(store)
    with synthetic_memory_access(channels=("hub-memory-tree",), scopes=(MemoryScope.task,)) as (policies, _audit):
        original = store._boundary.recheck

        def revoke(previous):
            policies.value = None
            original(previous)

        with monkeypatch.context() as patch:
            patch.setattr(store._boundary, "recheck", revoke)
            with pytest.raises(MemoryAccessError):
                service.quarantine(chunk.id, reason_code="suspected_injection")
    assert len(events()) == 0
    assert store.get_node("source", "source:source") is not None


def test_actual_retrieval_hides_quarantined_derivation_and_preserves_other_source(store):  # noqa: F811
    from agent.services.memory_tree_retrieval_service import MemoryTreeRetrievalService

    chunk = seed(store)
    other, _ = store.ingest_chunk(source_id="other", source_type="doc", label="other", content="safe fact")
    store.update_lifecycle(other.id, "sealed")
    retrieval = MemoryTreeRetrievalService(store=store)
    assert retrieval.retrieve_source("source").summary_node is not None
    MemoryQuarantineService(store).quarantine(chunk.id, reason_code="suspected_injection")
    hidden = retrieval.retrieve_source("source", lifecycle=None)
    assert hidden.chunks == [] and hidden.summary_node is None
    assert retrieval.retrieve_source("other").chunks[0].content == "safe fact"


def test_admission_cannot_supply_a_quarantine_release_record(store):  # noqa: F811
    chunk, _ = store.ingest_chunk(
        source_id="source", source_type="doc", label="leaf", content="fact",
        metadata={"memory_quarantine": {"prior_lifecycle": "sealed", "schema": "ananta.memory-quarantine.v1"}},
    )
    assert "memory_quarantine" not in chunk.chunk_metadata
    assert MemoryQuarantineService(store).release(chunk.id, reason_code="review_cleared") is False
    assert events() == []
