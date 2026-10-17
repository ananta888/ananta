"""Deterministic quality gates for self-development publications -- no model involved.

Live, two of five published python files had syntax errors (``dataclass`` without ``@``; a
docstring closed with one quote), a published shell gate script was not executable, and a
published ``tests/e2e`` file could never be covered by its profile verification because it
lies outside the profile's test tree. Both gates exist to make those defects impossible to
publish as "verified".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.self_development.contracts import task_contract
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.workspace import (
    git,
    validate_source_files,
)


# --- the source gate: every change must parse ---------------------------------------


def _write(root: Path, relative: str, text: str, *, executable: bool = False) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    if executable:
        path.chmod(0o755)


def test_a_change_that_does_not_parse_is_rejected(tmp_path):
    _write(tmp_path, 'agent/a.py', 'dataclass MeasurementStep:\n    name: str\n')  # missing @
    with pytest.raises(ValueError, match='source_syntax_invalid:agent/a.py'):
        validate_source_files(tmp_path, ['agent/a.py'])

    _write(tmp_path, 'tests/e2e/b.py', 'def test_x():\n    """opened with three.\n')
    with pytest.raises(ValueError, match='source_syntax_invalid:tests/e2e/b.py'):
        validate_source_files(tmp_path, ['tests/e2e/b.py'])


def test_a_shell_gate_must_be_executable_and_parse(tmp_path):
    _write(tmp_path, 'scripts/gates/soak.sh', '#!/bin/bash\necho ok\n')  # not executable
    with pytest.raises(ValueError, match='source_script_not_executable:scripts/gates/soak.sh'):
        validate_source_files(tmp_path, ['scripts/gates/soak.sh'])

    _write(tmp_path, 'scripts/gates/soak.sh', '#!/bin/bash\nif true\necho broken\n', executable=True)
    with pytest.raises(ValueError, match='source_syntax_invalid:scripts/gates/soak.sh'):
        validate_source_files(tmp_path, ['scripts/gates/soak.sh'])


def test_invalid_json_is_rejected_and_sound_changes_pass(tmp_path):
    _write(tmp_path, 'todos/todo.x.json', '{not json')
    with pytest.raises(ValueError, match='source_json_invalid:todos/todo.x.json'):
        validate_source_files(tmp_path, ['todos/todo.x.json'])

    _write(tmp_path, 'agent/a.py', '@dataclass\nclass MeasurementStep:\n    name: str\n')
    _write(tmp_path, 'todos/todo.y.json', json.dumps({'tasks': []}))
    _write(tmp_path, 'scripts/gates/ok.sh', '#!/bin/bash\necho ok\n', executable=True)
    validate_source_files(tmp_path, ['agent/a.py', 'todos/todo.y.json', 'scripts/gates/ok.sh'])


# --- the profile coverage gate: profile_passed must be able to speak for the change --


def _policy(tmp_path: Path) -> DevelopmentPolicy:
    control = tmp_path / 'private'
    (control / 'checkout').mkdir(parents=True)
    git(control / 'checkout', 'init', '-b', 'ananta/self-development')
    value = {
        'enabled': True,
        'automatic_publication': True,
        'remote_url': 'https://github.com/ananta888/ananta.git',
        'branch': 'ananta/self-development',
        'control_root': str(control),
        'workspace_root': str(tmp_path / 'workers'),
        'team_id': 'team',
        'tenant_id': 'test-tenant',
        'max_tasks': 2,
        'max_seconds': 1800,
        'max_attempts': 2,
        'allowed_roots': ['tests', 'docs', 'scripts'],
        'test_profiles': {
            'self-development': {'command': 'python -m pytest tests/self_development'},
            'all-tests': {'command': 'python -m pytest tests'},
        },
    }
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return DevelopmentPolicy(path)


def _contract(files: list[str], profile: str) -> dict:
    return task_contract({
        'execution': {
            'files': files,
            'test_profile': profile,
            'commit_message': 'test(autopilot): cover requested behavior',
            'completion_rule': 'profile_passed',
        }
    })


def test_a_test_file_outside_its_profile_tree_is_rejected(tmp_path):
    policy = _policy(tmp_path)
    with pytest.raises(ValueError, match='test_profile_does_not_cover_files'):
        policy.authorize_contract(_contract(['tests/e2e/test_vertical.py'], 'self-development'))


def test_a_test_file_inside_its_profile_tree_is_accepted(tmp_path):
    policy = _policy(tmp_path)
    profile = policy.authorize_contract(
        _contract(['tests/self_development/test_vertical.py'], 'self-development'))
    assert profile['command'] == 'python -m pytest tests/self_development'


def test_a_profile_running_the_whole_tree_covers_any_test_file(tmp_path):
    policy = _policy(tmp_path)
    policy.authorize_contract(_contract(['tests/e2e/test_vertical.py'], 'all-tests'))


def test_non_test_files_are_verified_behaviourally_and_stay_allowed(tmp_path):
    policy = _policy(tmp_path)
    policy.authorize_contract(_contract(['docs/architecture/note.md', 'scripts/gates/ok.sh'],
                                        'self-development'))
