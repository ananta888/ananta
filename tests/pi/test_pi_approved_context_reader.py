"""Current approved context uses one SQL transaction and existing CAP projection."""

import copy

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import TaskDB
from agent.db_models.blueprints import ContextBundleDB
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.services.pi_approved_context_reader import TransactionPiApprovedContextReader
from tests.native.test_native_context_policy import build_policy_setup


@pytest.mark.parametrize('mutation', [None, 'owner', 'revoked', 'policy_digest', 'command'])
def test_actual_task_bundle_policy_projection_is_caller_transaction_bound(tmp_path, monkeypatch, mutation):
    setup = build_policy_setup(monkeypatch)
    _, scope, bundle_data, command, policies, destinations, _ = setup
    snapshot = policies.active.return_value
    engine = create_engine('sqlite:///' + str(tmp_path / 'context.sqlite3'))
    SQLModel.metadata.create_all(engine)
    task = TaskDB(id='context-test-task', tenant_id=scope['tenant_id'], project_id=scope['project_id'],
        task_kind='pi_coding_agent', derivation_reason='native_graph_hub_delegation',
        context_bundle_id='context-test-bundle',
        status='assigned', worker_execution_context={'schema': 'ananta.native_graph_worker_context.v1',
            'runtime_path': 'native_graph_node', 'native_node_command': command.to_dict()})
    bundle = ContextBundleDB(id=task.context_bundle_id, task_id=task.id, **copy.deepcopy(bundle_data))
    row = ContextPolicyVersionDB(record_id='context-test-policy', tenant_id=scope['tenant_id'],
        project_id=scope['project_id'], policy_id=snapshot.policy_id, version=snapshot.version, state='active',
        document_json=snapshot.document, policy_digest=snapshot.policy_digest, etag=snapshot.etag,
        created_by=snapshot.created_by, created_at=snapshot.created_at,
        updated_by=snapshot.created_by, updated_at=snapshot.created_at)
    if mutation == 'owner':
        bundle.task_id = 'foreign-task'
    elif mutation == 'revoked':
        row.state = 'revoked'
    elif mutation == 'policy_digest':
        row.policy_digest = 'f'*64
    with Session(engine) as session, session.begin():
        session.add_all([task, bundle, row])
    reader = TransactionPiApprovedContextReader(destinations=destinations)
    try:
        with Session(engine) as session, session.begin():
            stored = session.get(TaskDB, 'context-test-task')
            if mutation == 'command':
                from dataclasses import replace

                command = replace(command, command_id='foreign-command')
            if mutation is None:
                result = reader.read(session=session, task=stored, command=command, worker_id='worker-1')
                assert 'def example(): return 1' in result.content
                assert 'unapproved assembled' not in result.content
                assert session.in_transaction()
            else:
                with pytest.raises(ValueError):
                    reader.read(session=session, task=stored, command=command, worker_id='worker-1')
        with Session(engine) as session:
            with pytest.raises(ValueError, match='caller_transaction_required'):
                reader.read(session=session, task=task, command=command, worker_id='worker-1')
    finally:
        engine.dispose()
