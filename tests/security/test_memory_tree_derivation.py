"""Actual SQL tree ancestry with explicit synthetic, headless Hub grants."""

import hashlib
import json

import pytest
from sqlmodel import Session

import agent.database as database
from agent.db_models import MemoryTreeChunkDB
from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_tree_summary_service import MemoryTreeSummaryService
from agent.services.memory_vault_export_service import MemoryVaultExportService
from ananta_contracts.memory_access import MemoryScope
from tests.security.memory_fixtures import synthetic_memory_access
from tests.test_memory_tree_store_service import store as store  # noqa: F401


def test_summary_keeps_protected_observed_chunk_hashes_and_updates_metadata(store):  # noqa: F811
    chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc',
                                 label='Synthetic leaf', content='synthetic private content canary')
    summaries = MemoryTreeSummaryService(store=store)
    summaries.build_source_summary('synthetic-source')
    node = store.get_node('source', 'source:synthetic-source')
    origin = node.node_metadata['memory_provenance']
    assert origin['operation'] == 'append'
    assert origin['derivation']['parents'] == [
        {'chunk_id': chunk.id, 'content_digest': hashlib.sha256(chunk.content.encode()).hexdigest()}]
    assert 'synthetic private content canary' not in json.dumps(origin)
    updated = store.upsert_node(node_type='source', label=node.label, summary='Updated summary',
                               metadata={'summary_version': '2', 'memory_provenance': {'grounding_state': 'verified'}})
    origin = updated.node_metadata['memory_provenance']
    assert origin['operation'] == 'update'
    assert origin['grounding_state'] == 'unverified'
    assert origin['derivation']['summary_digest'] == hashlib.sha256(b'Updated summary').hexdigest()
    assert origin['derivation']['parents'][0]['chunk_id'] == chunk.id
    assert updated.node_metadata['summary_version'] == '2'
    assert origin['production_release_evidence'] is False


def test_missing_old_parent_blocks_update_and_rolls_back_summary(store):  # noqa: F811
    chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc', label='leaf', content='x')
    store.upsert_node(node_type='source', label='summary', summary='original', child_chunk_ids=[chunk.id])
    with Session(database.engine) as session:
        session.delete(session.get(MemoryTreeChunkDB, chunk.id))
        session.commit()
    with pytest.raises(MemoryAccessError, match='memory_tree_child_namespace_mismatch'):
        store.upsert_node(node_type='source', label='summary', summary='must not persist')
    assert store.get_node('source', 'summary') is None
    from sqlmodel import select

    from agent.db_models import MemoryTreeNodeDB

    with Session(database.engine) as session:
        assert session.exec(select(MemoryTreeNodeDB).where(MemoryTreeNodeDB.label == 'summary')).one().summary == 'original'


def test_foreign_task_parent_is_not_attached(store):  # noqa: F811
    with synthetic_memory_access(task_id='other-task', channels=('hub-memory-tree',), scopes=(MemoryScope.task,)):
        chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc', label='leaf', content='x')
    with pytest.raises(MemoryAccessError, match='memory_tree_child_namespace_mismatch'):
        store.upsert_node(node_type='source', label='summary', child_chunk_ids=[chunk.id])
    assert store.get_node('source', 'summary') is None


def test_node_cannot_be_a_chunk_parent_or_form_a_cycle(store):  # noqa: F811
    node = store.upsert_node(node_type='source', label='summary', summary='original')
    with pytest.raises(MemoryAccessError, match='memory_tree_child_namespace_mismatch'):
        store.upsert_node(node_type='source', label='summary', child_chunk_ids=[node.id])
    assert store.get_node('source', 'summary').child_chunk_ids == []


def test_update_grant_loss_before_commit_rolls_back_content_and_origin(store, monkeypatch):  # noqa: F811
    node = store.upsert_node(node_type='source', label='summary', summary='original')
    original_origin = node.node_metadata['memory_provenance']
    recheck = store._boundary.recheck

    def revoke_update(decision):
        if decision.operation.value == 'update':
            raise MemoryAccessError('memory_run_inactive')
        return recheck(decision)

    monkeypatch.setattr(store._boundary, 'recheck', revoke_update)
    with pytest.raises(MemoryAccessError, match='memory_run_inactive'):
        store.upsert_node(node_type='source', label='summary', summary='must not persist')
    restored = store.get_node('source', 'summary')
    assert restored.summary == 'original'
    assert restored.node_metadata['memory_provenance'] == original_origin


def test_vault_preserves_source_origin_and_separate_export_origin(store, tmp_path):  # noqa: F811
    chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc', label='leaf', content='content')
    result = MemoryVaultExportService(export_root=tmp_path, store=store).export(
        cfg={'memory_vault_export': {'enabled': True}}, source_ids=['synthetic-source'])
    assert result['written'] == 1
    text = next(tmp_path.rglob('*.md')).read_text()
    header = text.split('---', 2)[1]
    fields = dict(line.split(': ', 1) for line in header.strip().splitlines())
    assert json.loads(fields['memory_access_origin']) == chunk.chunk_metadata['memory_provenance']
    destination = json.loads(fields['export_access_origin'])
    assert destination['channel_id'] == 'hub-memory-vault'
    assert destination['namespace'] == chunk.chunk_metadata['memory_namespace']
    assert destination['grounding_state'] == 'unverified'
    assert destination['production_release_evidence'] is False
    assert fields['content_digest'] == hashlib.sha256(chunk.content.encode()).hexdigest()
    assert fields['grounding_state'] == 'unverified' and fields['production_release_evidence'] == 'false'


def test_vault_does_not_invent_missing_legacy_origin(store, tmp_path):  # noqa: F811
    chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc', label='leaf', content='content')
    with Session(database.engine) as session:
        persisted = session.get(MemoryTreeChunkDB, chunk.id)
        metadata = dict(persisted.chunk_metadata)
        metadata.pop('memory_provenance')
        persisted.chunk_metadata = metadata
        session.add(persisted)
        session.commit()
    result = MemoryVaultExportService(export_root=tmp_path, store=store).export(
        cfg={'memory_vault_export': {'enabled': True}}, source_ids=['synthetic-source'])
    assert result['written'] == 1
    text = next(tmp_path.rglob('*.md')).read_text()
    assert 'memory_access_origin: null' in text
    assert 'grounding_state: unverified' in text


def test_tree_update_with_separate_sql_audit_transaction_keeps_exact_issued_reference(store, tmp_path, monkeypatch):  # noqa: F811
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.memory_audit import MemoryAuditEventDB
    from agent.repositories.memory_audit import SQLMemoryAuditRepository, validate_memory_audit_event
    from agent.services.memory_access_context import MemoryAccessContext, bind_memory_access, current_memory_access
    from agent.services.memory_audit_service import MemoryAuditService
    from agent.services.memory_channel_registry import MemoryChannelRegistry
    from agent.services.memory_policy_gateway import MemoryPolicyGateway
    from tests.security.memory_fixtures import PolicyFixture

    engine = create_engine(f"sqlite:///{tmp_path / 'tree-origin.sqlite'}", connect_args={'timeout': 1})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, 'engine', engine)
    context = current_memory_access()
    admitted = context.gateway._policies.current(actor=context.actor, run_id=context.run_id)
    repository = SQLMemoryAuditRepository(engine)
    gate = MemoryPolicyGateway(policies=PolicyFixture(admitted), audit=MemoryAuditService(repository=repository),
                               channels=MemoryChannelRegistry(frozenset({'hub-memory-tree', 'hub-memory-vault'})))
    try:
        with bind_memory_access(MemoryAccessContext(context.actor, context.run_id, gate)):
            chunk, _ = store.ingest_chunk(source_id='synthetic-source', source_type='doc', label='leaf', content='x')
            store.upsert_node(node_type='source', label='summary', summary='original', child_chunk_ids=[chunk.id])
            node = store.upsert_node(node_type='source', label='summary', summary='updated')
            origin = node.node_metadata['memory_provenance']
            assert origin['origin_state'] == 'audit_referenced' and origin['operation'] == 'update'
            with Session(engine) as session:
                event = session.get(MemoryAuditEventDB, origin['decision_audit_ref'])
                validate_memory_audit_event(event)
                assert event.payload['operation'] == 'update'
                assert event.payload['target']['namespace'] == origin['namespace']
            assert repository.verify(tenant_id=context.actor.tenant_id)['verified'] is True
    finally:
        engine.dispose()


@pytest.mark.parametrize('children', [['0' * 32] * 2001, ['unknown'], ['a' * 31], 'a' * 32, [None]])
def test_invalid_or_unbounded_parent_requests_fail_before_node_creation(store, children):  # noqa: F811
    with pytest.raises(ValueError, match='memory_tree_children_invalid'):
        store.upsert_node(node_type='source', label='summary', child_chunk_ids=children)
    assert store.get_node('source', 'summary') is None
