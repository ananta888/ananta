"""Closed operator task contracts; model output cannot expand authorization."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

DONE = frozenset({'done', 'completed', 'cancelled', 'archived', 'skipped'})
ROOTS = frozenset({'agent', 'worker', 'tests', 'scripts', 'docs', 'todos'})


def source_path(value: str) -> str:
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('invalid_source_path')
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or '..' in path.parts:
        raise ValueError('invalid_source_path')
    if path.parts[0] not in ROOTS or any(p.startswith('.') for p in path.parts):
        raise ValueError('protected_source_path')
    if any(p in {'fixtures', 'secrets', 'node_modules', '__pycache__'} for p in path.parts):
        raise ValueError('protected_source_path')
    if any(c in value for c in '*?[]\x00\n\r') or len(path.parts) < 2:
        raise ValueError('invalid_source_path')
    return value


def task_contract(item: dict) -> dict:
    """An explicit execution block is required for unattended legacy TODOs."""
    spec = item.get('execution')
    if not isinstance(spec, dict):
        raise ValueError('needs_execution_contract')
    files = spec.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 12:
        raise ValueError('needs_explicit_files')
    files = sorted({source_path(p) for p in files})
    profile = spec.get('test_profile')
    if not isinstance(profile, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', profile):
        raise ValueError('needs_test_profile')
    message = spec.get('commit_message', '')
    if not isinstance(message, str) or not re.fullmatch(
        r'(feat|fix|test|docs|refactor|chore)\([a-z0-9-]+\): [^\n\r]{8,120}', message
    ):
        raise ValueError('needs_scoped_commit_message')
    if spec.get('completion_rule') != 'profile_passed':
        raise ValueError('needs_completion_rule')
    from agent.services.commit_message_validator import CommitMessageValidator
    if not CommitMessageValidator().validate(message).valid:
        raise ValueError('invalid_commit_message')
    return {'files': files, 'test_profile': profile, 'commit_message': message,
            'completion_rule': 'profile_passed'}


def flatten_tasks(document):
    if isinstance(document, dict):
        tasks = document.get('tasks', [])
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict) and task.get('id'):
                    yield task
        for section in document.get('sections', []) if isinstance(document.get('sections'), list) else []:
            yield from flatten_tasks(section)


def form() -> dict:
    return {'tasks': [{'id': 'MY-001', 'title': 'Describe the requested change',
        'description': 'Describe expected behavior and acceptance criteria.', 'status': 'todo',
        'depends_on': [], 'execution': {'files': ['tests/self_development/test_example.py'],
        'test_profile': 'self-development', 'commit_message': 'test(autopilot): cover requested behavior',
        'completion_rule': 'profile_passed'}}]}
