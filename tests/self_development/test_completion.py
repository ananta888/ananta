"""Deterministic TODO synchronisation: mark done, and archive what is fully worked off.

A document completes through publication (``seal_completion`` marks its task done); when every
task is done the document moves to ``todos/archiv`` in the same closed change set. Documents
that were already done before this loop existed are archived by a deterministic sweep at
``import_tasks`` time. No model is involved anywhere.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.services.self_development.completion import (
    archive_completed_sources,
    archive_source,
    is_fully_done,
    seal_completion,
)
from agent.services.self_development.workspace import git


def _document(*statuses: str, status: str | None = None) -> dict:
    document = {
        'tasks': [
            {'id': f'T-{index}', 'status': value, 'title': f'task {index}',
             'execution': {'files': ['tests/x.py'], 'test_profile': 'self-development',
                            'commit_message': 'test(autopilot): cover one case',
                            'completion_rule': 'profile_passed'}}
            for index, value in enumerate(statuses)
        ]
    }
    if status is not None:
        document['status'] = status
    return document


def _policy(root: Path, checkout: Path) -> SimpleNamespace:
    return SimpleNamespace(root=root, checkout=checkout, assert_current=lambda: None,
                           value={'max_attempts': 2, 'max_seconds': 0, 'max_tasks': 0, 'team_id': 'team'})


# --- is_fully_done -------------------------------------------------------------------


def test_a_document_is_done_only_when_every_task_is_done():
    assert is_fully_done(_document('done', 'done'))
    assert is_fully_done(_document('completed', 'cancelled'))
    assert not is_fully_done(_document('done', 'todo'))
    assert not is_fully_done(_document('in_progress'))
    # a document without tasks is judged by its own status
    assert is_fully_done({'status': 'done'})
    assert is_fully_done({'status': 'skipped'})
    assert not is_fully_done({'status': 'todo'})
    assert not is_fully_done({})
    assert not is_fully_done(_document())


# --- archive_source ------------------------------------------------------------------


def test_archive_source_maps_active_and_root_todos_into_the_archive():
    assert archive_source('todos/active/todo.x.json') == 'todos/archiv/todo.x.json'
    assert archive_source('todos/todo.y.json') == 'todos/archiv/todo.y.json'
    # a nested track keeps its subpath so names cannot collide
    assert archive_source('todos/feature/todo.z.json') == 'todos/archiv/feature/todo.z.json'
    # already archived documents need no move at all
    assert archive_source('todos/archiv/todo.x.json') is None
    assert archive_source('todos/archive/todo.x.json') is None


# --- seal_completion -----------------------------------------------------------------


def _sealed_item(document: dict, source: str) -> dict:
    return {
        'task_id': 'task-1',
        'source': source,
        'todo_id': document['tasks'][0]['id'],
        'source_execution': document['tasks'][0]['execution'],
        'outcome': {'run_id': 'run-1', 'source_ids': ['src-1'], 'result_digest': 'digest'},
        'hashes': {'tests/x.py': 'deadbeef'},
    }


def test_seal_completion_marks_the_task_done_and_keeps_the_document_in_place(tmp_path):
    checkout = tmp_path / 'checkout'
    (checkout / 'todos' / 'active').mkdir(parents=True)
    source = 'todos/active/todo.partial.json'
    document = _document('todo', 'todo')
    (checkout / source).write_text(json.dumps(document), encoding='utf-8')
    policy = _policy(tmp_path / 'private', checkout)
    item = _sealed_item(document, source)
    item['todo_id'] = 'T-0'

    seal_completion(policy, item)

    assert item['hashes'][source]  # the document is part of the change set, at its own path
    assert 'removed' not in item
    sealed = json.loads((policy.root / 'sealed' / item['task_id'] / source).read_text(encoding='utf-8'))
    assert sealed['tasks'][0]['status'] == 'done'
    assert sealed['tasks'][0]['execution_result']['completion_rule'] == 'profile_passed'
    assert sealed['tasks'][1]['status'] == 'todo'  # siblings untouched


def test_seal_completion_moves_a_fully_worked_off_document_to_the_archive(tmp_path):
    checkout = tmp_path / 'checkout'
    (checkout / 'todos' / 'active').mkdir(parents=True)
    source = 'todos/active/todo.done.json'
    document = _document('done', 'done')
    (checkout / source).write_text(json.dumps(document), encoding='utf-8')
    policy = _policy(tmp_path / 'private', checkout)
    item = _sealed_item(document, source)
    item['todo_id'] = 'T-0'

    seal_completion(policy, item)

    archived = 'todos/archiv/todo.done.json'
    # the worker's changed files stay in the change set; the old todo path is removed
    assert set(item['hashes']) == {'tests/x.py', archived}
    assert source not in item['hashes']
    assert item['removed'] == [source]
    sealed = json.loads((policy.root / 'sealed' / item['task_id'] / archived).read_text(encoding='utf-8'))
    assert sealed['status'] == 'done'


def test_seal_completion_refuses_to_overwrite_an_existing_archive_document(tmp_path):
    checkout = tmp_path / 'checkout'
    (checkout / 'todos' / 'active').mkdir(parents=True)
    (checkout / 'todos' / 'archiv').mkdir(parents=True)
    source = 'todos/active/todo.done.json'
    (checkout / source).write_text(json.dumps(_document('done')), encoding='utf-8')
    (checkout / 'todos' / 'archiv' / 'todo.done.json').write_text('{"tasks": []}', encoding='utf-8')
    policy = _policy(tmp_path / 'private', checkout)
    item = _sealed_item(_document('done'), source)
    item['todo_id'] = 'T-0'

    with pytest.raises(ValueError, match='todo_archive_path_conflict'):
        seal_completion(policy, item)


# --- archive_completed_sources (the deterministic sweep) -----------------------------


class _RecordingPublisher:
    """A publisher fake that mirrors the file effects of the real one (add + delete)."""

    def __init__(self, policy_root: Path, checkout: Path):
        self.policy_root = policy_root
        self.checkout = checkout
        self.items = []

    def publish(self, item):
        self.items.append(dict(item))
        seal = self.policy_root / 'sealed' / item['task_id']
        for relative in item['hashes']:
            target = self.checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((seal / relative).read_bytes())
        for relative in item.get('removed') or []:
            (self.checkout / relative).unlink()
        item['status'] = 'published'


def _checkout_with_todos(tmp_path: Path) -> Path:
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    git(checkout, 'init', '-b', 'ananta/self-development')
    git(checkout, 'config', 'user.name', 'Test')
    git(checkout, 'config', 'user.email', 'test@example.invalid')
    (checkout / 'todos' / 'active').mkdir(parents=True)
    (checkout / 'todos' / 'active' / 'todo.done.json').write_text(
        json.dumps(_document('done', 'done')), encoding='utf-8')
    (checkout / 'todos' / 'active' / 'todo.open.json').write_text(
        json.dumps(_document('done', 'todo')), encoding='utf-8')
    git(checkout, 'add', '-A')
    git(checkout, 'commit', '-m', 'docs(docs): establish synthetic test baseline')
    git(checkout, 'remote', 'add', 'origin', 'https://github.com/ananta888/ananta.git')
    return checkout


def test_the_sweep_archives_only_the_documents_that_are_fully_worked_off(tmp_path):
    checkout = _checkout_with_todos(tmp_path)
    policy = _policy(tmp_path / 'private', checkout)
    publisher = _RecordingPublisher(tmp_path / 'private', checkout)

    moved = archive_completed_sources(policy, publish=publisher.publish)

    assert moved == ['todos/active/todo.done.json']
    assert len(publisher.items) == 1
    item = publisher.items[0]
    assert set(item['hashes']) == {'todos/archiv/todo.done.json'}
    assert item['removed'] == ['todos/active/todo.done.json']
    assert item['contract']['commit_message'].startswith('chore(self-development): archive ')

    # nothing left to do on a second sweep
    assert archive_completed_sources(policy, publish=publisher.publish) == []
    assert len(publisher.items) == 1


# --- the publication of a move (real git) --------------------------------------------


class _GitOps:
    """A GitOps double: real git for stage/commit, no network for push."""

    def __init__(self, checkout: Path):
        self.checkout = checkout
        self.pushes = []

    def stage(self, workspace_id, paths, **kwargs):
        git(self.checkout, 'add', '--', *paths)
        return SimpleNamespace(ok=True, error=None)

    def commit(self, workspace_id, message, **kwargs):
        git(self.checkout, 'commit', '-m', message)
        return SimpleNamespace(ok=True, error=None)

    def push(self, workspace_id, **kwargs):
        self.pushes.append(kwargs)
        return SimpleNamespace(ok=True, error=None)


def test_the_publication_moves_the_document_and_stages_the_deletion(tmp_path):
    from agent.services.self_development.publisher import Publisher

    checkout = _checkout_with_todos(tmp_path)
    root = tmp_path / 'private'
    source = 'todos/active/todo.done.json'
    archived = 'todos/archiv/todo.done.json'
    content = (checkout / source).read_bytes()
    seal = root / 'sealed' / 'task-move' / archived
    seal.parent.mkdir(parents=True, exist_ok=True)
    seal.write_bytes(content)
    seal.chmod(0o400)
    policy = SimpleNamespace(
        assert_current=lambda: None,
        value={'branch': 'ananta/self-development',
               'remote_url': 'https://github.com/ananta888/ananta.git',
               'github_token_file': str(tmp_path / 'token')},
        checkout=checkout,
        root=root,
    )
    service = _GitOps(checkout)
    publisher = Publisher(policy, service_factory=lambda **_kwargs: service)
    item = {
        'task_id': 'task-move',
        'contract': {'commit_message': 'chore(self-development): archive 1 completed todo document(s)'},
        'hashes': {archived: __import__('hashlib').sha256(content).hexdigest()},
        'removed': [source],
        'revision': git(checkout, 'rev-parse', 'HEAD'),
    }

    publisher.publish(item, lambda: None, lambda: None)

    assert item['status'] == 'published'
    assert not (checkout / source).exists()
    assert (checkout / archived).read_bytes() == content
    assert git(checkout, 'log', '-1', '--pretty=%s') == 'chore(self-development): archive 1 completed todo document(s)'
    assert git(checkout, 'status', '--porcelain') == ''  # the move is exactly the change set
    assert service.pushes == [{'remote': 'origin', 'branch': 'ananta/self-development',
                               'credential_ref': 'secret://self-development/github'}]


def test_the_publication_refuses_a_removal_of_a_missing_path(tmp_path):
    from agent.services.self_development.publisher import Publisher

    checkout = _checkout_with_todos(tmp_path)
    root = tmp_path / 'private'
    (root / 'sealed' / 'task-bad').mkdir(parents=True)
    policy = SimpleNamespace(
        assert_current=lambda: None,
        value={'branch': 'ananta/self-development',
               'remote_url': 'https://github.com/ananta888/ananta.git',
               'github_token_file': str(tmp_path / 'token')},
        checkout=checkout,
        root=root,
    )
    publisher = Publisher(policy, service_factory=lambda **_kwargs: _GitOps(checkout))
    item = {
        'task_id': 'task-bad',
        'contract': {'commit_message': 'chore(self-development): archive nothing'},
        'hashes': {},
        'removed': ['todos/active/todo.missing.json'],
        'revision': git(checkout, 'rev-parse', 'HEAD'),
    }

    with pytest.raises(ValueError, match='publication_removed_path_missing'):
        publisher.publish(item, lambda: None, lambda: None)


# --- unblock_satisfied_tasks: deterministic release of finished waits --------------------


def test_a_blocked_task_is_released_when_its_dependencies_are_done():
    from agent.services.self_development.completion import unblock_satisfied_tasks

    document = {
        'tasks': [
            {'id': 'A', 'status': 'done', 'depends_on': []},
            {'id': 'B', 'status': 'done', 'depends_on': []},
            {'id': 'C', 'status': 'blocked', 'depends_on': ['A', 'B']},
            {'id': 'D', 'status': 'blocked', 'depends_on': ['A', 'C']},
            {'id': 'E', 'status': 'blocked', 'depends_on': []},
            {'id': 'F', 'status': 'needs_review', 'depends_on': []},
            {'id': 'G', 'status': 'paused', 'depends_on': []},
            {'id': 'H', 'status': 'blocked_by_dependency', 'depends_on': []},
        ]
    }

    assert unblock_satisfied_tasks(document) is True
    by_id = {task['id']: task['status'] for task in document['tasks']}
    assert by_id['C'] == 'todo'  # all dependencies done
    assert by_id['D'] == 'blocked'  # C is not done yet
    assert by_id['E'] == 'blocked'  # no dependency: blocked for a stated reason of its own
    assert by_id['F'] == 'needs_review'  # a human gate is never auto-released
    assert by_id['G'] == 'paused'  # an operator decision is never auto-released
    assert by_id['H'] == 'todo'  # declared to wait for dependencies, but it has none


def test_a_document_without_satisfied_waits_is_untouched():
    from agent.services.self_development.completion import unblock_satisfied_tasks

    document = {'tasks': [
        {'id': 'A', 'status': 'done'},
        {'id': 'B', 'status': 'blocked', 'depends_on': ['A', 'MISSING']},
    ]}
    assert unblock_satisfied_tasks(document) is False
    assert document['tasks'][1]['status'] == 'blocked'


def test_the_unblock_sweep_publishes_one_closed_change_set(tmp_path):
    from agent.services.self_development.completion import unblock_satisfied_sources

    checkout = _checkout_with_todos(tmp_path)
    (checkout / 'todos' / 'active' / 'todo.waiting.json').write_text(json.dumps({
        'tasks': [
            {'id': 'A', 'status': 'done', 'depends_on': []},
            {'id': 'B', 'status': 'blocked', 'depends_on': ['A']},
        ],
    }), encoding='utf-8')
    git(checkout, 'add', '-A')
    git(checkout, 'commit', '-m', 'docs(docs): add a waiting todo')
    policy = _policy(tmp_path / 'private', checkout)
    publisher = _RecordingPublisher(tmp_path / 'private', checkout)

    moved = unblock_satisfied_sources(policy, publish=publisher.publish)

    assert moved == ['todos/active/todo.waiting.json']
    assert len(publisher.items) == 1
    assert set(publisher.items[0]['hashes']) == {'todos/active/todo.waiting.json'}
    assert publisher.items[0]['contract']['commit_message'].startswith('chore(self-development): unblock ')
    updated = json.loads((checkout / 'todos' / 'active' / 'todo.waiting.json').read_text(encoding='utf-8'))
    assert {task['id']: task['status'] for task in updated['tasks']} == {'A': 'done', 'B': 'todo'}
    # a second sweep has nothing left to do
    assert unblock_satisfied_sources(policy, publish=publisher.publish) == []


def test_the_publication_keeps_an_executable_sealed_file_executable(tmp_path):
    from agent.services.self_development.publisher import Publisher

    checkout = _checkout_with_todos(tmp_path)
    root = tmp_path / 'private'
    seal = root / 'sealed' / 'task-exec'
    seal.mkdir(parents=True, exist_ok=True)
    script = seal / 'scripts/gates/soak.sh'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(b'#!/bin/bash\necho ok\n')
    script.chmod(0o755)  # a sealed gate script must be able to run after publication
    digest = __import__('hashlib').sha256(script.read_bytes()).hexdigest()
    policy = SimpleNamespace(
        assert_current=lambda: None,
        value={'branch': 'ananta/self-development',
               'remote_url': 'https://github.com/ananta888/ananta.git',
               'github_token_file': str(tmp_path / 'token')},
        checkout=checkout,
        root=root,
    )
    service = _GitOps(checkout)
    publisher = Publisher(policy, service_factory=lambda **_kwargs: service)
    item = {
        'task_id': 'task-exec',
        'contract': {'commit_message': 'fix(self-development): make the gate script executable'},
        'hashes': {'scripts/gates/soak.sh': digest},
        'removed': [],
        'revision': git(checkout, 'rev-parse', 'HEAD'),
    }

    publisher.publish(item, lambda: None, lambda: None)

    published = checkout / 'scripts/gates/soak.sh'
    assert published.read_bytes() == b'#!/bin/bash\necho ok\n'
    assert os.access(published, os.X_OK)


def test_a_policy_change_rearms_only_the_items_it_invalidated(tmp_path):
    """``development_policy_changed`` is bookkeeping: those items are valid candidates again."""
    from agent.services.self_development.coordinator import DevelopmentCoordinator

    checkout = _checkout_with_todos(tmp_path)
    policy = _policy(tmp_path / 'private', checkout)

    class _Queue:
        def __init__(self):
            self.ingested = []

        def ingest_task(self, **kwargs):
            self.ingested.append(kwargs)

    queue = _Queue()
    coordinator = DevelopmentCoordinator(policy, queue=queue)
    state = {'session': {'id': 's', 'enabled': True, 'expires_at': None, 'dispatched': 0}, 'items': {
        'a': {'key': 'a', 'task_id': 't-a', 'source': 'todos/active/todo.done.json', 'todo_id': 'A',
              'title': 'a', 'description': '', 'original_status': 'todo', 'status': 'failed',
              'reason': 'development_policy_changed', 'dependencies': [], 'contract': {'files': ['tests/x.py']},
              'attempts': []},
        'b': {'key': 'b', 'task_id': 't-b', 'source': 'todos/active/todo.done.json', 'todo_id': 'B',
              'title': 'b', 'description': '', 'original_status': 'todo', 'status': 'failed',
              'reason': 'development_evidence_gate_failed:evidence_run_not_successful',
              'dependencies': [], 'contract': {'files': ['tests/y.py']}, 'attempts': []},
    }}

    class _Journal:
        def locked(self):
            import contextlib
            return contextlib.nullcontext()

        def read(self):
            return state

        def write(self, new_state):
            state.update(new_state)

    coordinator.journal = _Journal()

    assert coordinator.rearm_stale_policy_items() == 1
    assert state['items']['a']['status'] == 'ready'
    assert state['items']['b']['status'] == 'failed'  # a real failure stays failed
    assert len(queue.ingested) == 1  # exactly the re-armed item, with a fresh task id
    assert queue.ingested[0]['team_id'] == 'team'
