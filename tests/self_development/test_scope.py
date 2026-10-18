"""One policy profile drives contract roots, worker globs and test commands."""
import json

import pytest

from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.scope import (
    allowed_roots_for_contracts,
    merge_worker_mutation,
    profile_roots,
    required_worker_mutation,
)

VALUE = {
    'enabled': True, 'automatic_publication': True,
    'remote_url': 'https://github.com/ananta888/ananta.git', 'branch': 'ananta/self-development',
    'control_root': '/private', 'workspace_root': '/workers',
    'allowed_roots': ['agent', 'worker', 'tests'],
    'planning': {'enabled': True, 'allowed_test_profiles': ['a']},
    'test_profiles': {
        'a': {'command': 'pytest a', 'allowed_roots': ['agent', 'tests'],
              'allowed_new_file_globs': ['agent/**/*.py', 'tests/**/*.py']},
        'b': {'command': 'pytest b'},
    },
}


def test_profile_roots_fall_back_to_global_roots():
    assert profile_roots(VALUE, 'a') == ['agent', 'tests']
    assert profile_roots(VALUE, 'b') == ['agent', 'worker', 'tests']


def test_contract_roots_follow_planning_profiles():
    assert allowed_roots_for_contracts(VALUE) == ['agent', 'tests']
    assert allowed_roots_for_contracts(VALUE, ['a', 'b']) == ['agent', 'tests', 'worker']


def test_required_worker_mutation_merges_globs_and_commands():
    required = required_worker_mutation(VALUE)
    assert 'agent/**/*.py' in required['allowed_new_file_globs']
    assert 'worker/*' in required['allowed_new_file_globs']  # derived from b's roots
    assert required['allowlisted_test_commands'] == ['pytest a', 'pytest b']


def test_merge_worker_mutation_is_additive_and_idempotent():
    existing = {'allowed_new_file_globs': ['tests/self_development/*.py'],
                'allowlisted_test_commands': ['pytest a'], 'mutation_mode': 'controlled_workspace'}
    merged, changed = merge_worker_mutation(VALUE, existing)
    assert changed is True
    assert 'tests/self_development/*.py' in merged['allowed_new_file_globs']
    assert merged['mutation_mode'] == 'controlled_workspace'
    _, again = merge_worker_mutation(VALUE, merged)
    assert again is False


def _policy(tmp_path, **overrides):
    value = json.loads(json.dumps(VALUE))
    value['control_root'] = str(tmp_path / 'private')
    value['workspace_root'] = str(tmp_path / 'workers')
    value.update(overrides)
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(value))
    return DevelopmentPolicy(path)


def test_policy_rejects_profile_roots_outside_global(tmp_path):
    with pytest.raises(ValueError, match='development_profile_invalid'):
        _policy(tmp_path, test_profiles={'a': {'command': 'pytest a', 'allowed_roots': ['secret']}})


def _contract(files):
    return {'test_profile': 'a', 'files': files, 'commit_message': 'feat(agent): x',
            'completion_rule': 'profile_passed'}


def test_authorize_contract_uses_profile_roots(tmp_path):
    policy = _policy(tmp_path)
    assert policy.authorize_contract(_contract(['agent/x.py']))['command'] == 'pytest a'
    with pytest.raises(ValueError, match='task_scope_not_authorized'):
        policy.authorize_contract(_contract(['worker/y.py']))
