"""Deterministic operator contracts; no live services or approvals."""
import json

import pytest

from agent.services.self_development.catalog import import_document
from agent.services.self_development.contracts import form, source_path, task_contract
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.state import Journal


def policy(tmp_path):
    path = tmp_path / 'policy.json'
    value = {'enabled': True, 'automatic_publication': True,
        'remote_url': 'https://github.com/ananta888/ananta.git', 'branch': 'ananta/self-development',
        'control_root': str(tmp_path / 'private'), 'workspace_root': str(tmp_path / 'workers'),
        'max_tasks': 2, 'allowed_roots': ['tests'],
        'test_profiles': {'self-development': {'command': 'python -m pytest'}}}
    path.write_text(json.dumps(value))
    return DevelopmentPolicy(path)


def test_form_has_complete_execution_contract():
    contract = task_contract(form()['tasks'][0])
    assert contract['files'] == ['tests/self_development/test_example.py']


def test_legacy_todo_requires_explicit_execution_contract(tmp_path):
    records = import_document({'tasks': [{'id': 'ONE', 'status': 'partial'}]}, 'todo.json', policy(tmp_path))
    assert records[0]['status'] == 'blocked'
    assert records[0]['reason'] == 'needs_execution_contract'


def test_import_preserves_blocked_and_done_and_dependencies(tmp_path):
    document = form()
    document['tasks'][0].update(status='blocked', depends_on=['FINISHED', 'EXTERNAL'])
    document['tasks'].append({'id': 'FINISHED', 'status': 'done'})
    records = import_document(document, 'todo.json', policy(tmp_path))
    assert len(records) == 1
    assert records[0]['reason'] == 'source_task_blocked'
    assert records[0]['dependencies'] == ['EXTERNAL']


def test_import_identity_is_stable(tmp_path):
    p = policy(tmp_path)
    first = import_document(form(), 'todo.json', p)[0]
    document = form()
    document['tasks'][0]['status'] = 'partial'
    assert first['key'] == import_document(document, 'todo.json', p)[0]['key']


def test_operator_policy_changes_revoke_publication(tmp_path):
    p = policy(tmp_path)
    p.path.write_text('{}')
    with pytest.raises(ValueError, match='policy_changed'):
        p.assert_current()


def test_contract_cannot_request_unknown_test_profile(tmp_path):
    p = policy(tmp_path)
    contract = task_contract(form()['tasks'][0])
    contract['test_profile'] = 'arbitrary-shell'
    with pytest.raises(ValueError, match='test_profile_not_authorized'):
        p.authorize_contract(contract)


def test_duplicate_todo_ids_are_rejected(tmp_path):
    item = form()['tasks'][0]
    with pytest.raises(ValueError, match='duplicate_todo_identifier'):
        import_document({'tasks': [item, item]}, 'todo.json', policy(tmp_path))


def test_journal_atomic_round_trip(tmp_path):
    journal = Journal(tmp_path / 'journal')
    with journal.locked():
        state = journal.read()
        state['items']['task'] = {'status': 'published'}
        journal.write(state)
    assert journal.read() == state
    assert journal.path.stat().st_mode & 0o777 == 0o600


def test_path_must_name_source_file():
    assert source_path('agent/services/example.py') == 'agent/services/example.py'


def test_source_block_does_not_disappear_with_execution_contract(tmp_path):
    document = form()
    document['tasks'][0]['status'] = 'needs_review'
    item = import_document(document, 'todo.json', policy(tmp_path))[0]
    assert item['status'] == 'blocked'
    assert item['reason'] == 'source_task_needs_review'
