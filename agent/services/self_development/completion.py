"""Deterministic Hub-owned TODO completion metadata, after verified execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

from agent.services.self_development.contracts import DONE, flatten_tasks, source_path
from agent.services.self_development.workspace import read_source


def is_fully_done(document: dict) -> bool:
    """Whether the document is completely worked off.

    Every task of a ``tasks``/``sections`` document must be in ``DONE``; a document without
    tasks is judged by its own ``status``. Fully deterministic -- no model involved.
    """
    tasks = [task for task in flatten_tasks(document) if isinstance(task, dict)]
    if not tasks:
        return str(document.get('status', '')).lower() in DONE
    return all(str(task.get('status', '')).lower() in DONE for task in tasks)


def unblock_satisfied_tasks(document: dict) -> bool:
    """Deterministically release tasks whose declared prerequisites are all done.

    ``blocked`` means "waiting for my dependencies": once every ``depends_on`` task of the
    document is in ``DONE``, the wait is over and the task becomes ``todo`` again. Declarations
    of a different kind are never touched here -- ``needs_review`` is a human gate and ``paused``
    is an operator decision -- and neither is a ``blocked`` task without any dependency, which
    blocks for a stated reason of its own.
    """
    tasks = [task for task in flatten_tasks(document) if isinstance(task, dict)]
    done = {str(task.get('id')) for task in tasks if str(task.get('status', '')).lower() in DONE}
    changed = False
    for task in tasks:
        status = str(task.get('status', '')).lower()
        if status not in {'blocked', 'blocked_by_dependency'}:
            continue
        dependencies = task.get('depends_on', task.get('dependencies', [])) or []
        dependencies = [str(dep) for dep in dependencies if isinstance(dep, str)]
        waiting_for = [dep for dep in dependencies if dep not in done]
        if waiting_for:
            continue
        if dependencies or status == 'blocked_by_dependency':
            task['status'] = 'todo'
            changed = True
    return changed


def unblock_satisfied_sources(policy, *, publish) -> list[str]:
    """Deterministic bookkeeping: publish the release of satisfied TODO tasks.

    The 93 ``source_task_blocked`` items of a live run were exactly these: tasks declared to
    wait whose dependencies had long been published. Releasing them is bookkeeping -- the same
    closed change set discipline as the archive sweep, no model involved.
    """
    from agent.services.self_development.catalog import iter_todo_documents
    from agent.services.self_development.workspace import git

    changed: dict[str, bytes] = {}
    for source, document in iter_todo_documents(policy.checkout):
        if not unblock_satisfied_tasks(document):
            continue
        changed[source] = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()
    if not changed:
        return []
    task_id = 'todo-unblock-' + hashlib.sha256('\0'.join(sorted(changed)).encode()).hexdigest()[:16]
    seal = policy.root / 'sealed' / task_id
    seal.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for source, encoded in sorted(changed.items()):
        target = seal / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        target.chmod(0o400)
        hashes[source] = hashlib.sha256(encoded).hexdigest()
    item = {
        'task_id': task_id,
        'source': 'todos',
        'status': 'running',
        'contract': {
            'commit_message':
                f'chore(self-development): unblock {len(changed)} todo document(s) '
                'with satisfied dependencies',
        },
        'hashes': hashes,
        'removed': [],
        'revision': git(policy.checkout, 'rev-parse', 'HEAD'),
    }
    publish(item)
    return sorted(changed)


def archive_source(source: str) -> str | None:
    """Where a completed TODO document belongs, or ``None`` when it is already archived.

    ``todos/active/x.json`` -> ``todos/archiv/x.json``; a bare ``todo.y.json`` or a nested
    ``todos/feature/z.json`` keeps its subpath so names cannot collide. Sources already below
    ``todos/archive``/``todos/archiv`` need no move.
    """
    if not isinstance(source, str) or not source:
        return None
    parts = PurePosixPath(source).parts
    if parts[0] == 'todos':
        rest = parts[1:]
        if not rest:
            return None
        if rest[0] in {'archive', 'archiv'}:
            return None
        if rest[0] == 'active':
            rest = rest[1:]
    else:
        rest = parts
    if not rest:
        return None
    archived = str(PurePosixPath('todos', 'archiv', *rest))
    source_path(archived)  # defence in depth: the closed path rules apply here as everywhere
    return None if archived == source else archived


def seal_completion(policy, item):
    source = item['source']
    if source == 'user-submission':
        return
    source_path(source)
    if not source.startswith('todos/') and not source.startswith('todo.'):
        raise ValueError('todo_completion_source_denied')
    document = json.loads(read_source(policy.checkout, source))
    matches = [t for t in flatten_tasks(document) if str(t['id']) == item['todo_id']]
    if len(matches) != 1:
        raise ValueError('todo_completion_contract_changed')
    task = matches[0]
    if item.get('planned'):
        # A Hub-planned contract is recorded on first verified publication.
        if task.get('execution') is not None:
            raise ValueError('todo_completion_contract_changed')
        task['execution'] = item['source_execution']
    elif task.get('execution') != item.get('source_execution'):
        raise ValueError('todo_completion_contract_changed')
    task['status'] = 'done'
    task['execution_result'] = {'scope': 'local', 'run_id': item['outcome']['run_id'],
        'source_ids': item['outcome']['source_ids'], 'result_digest': item['outcome']['result_digest'],
        'completion_rule': 'profile_passed'}
    archived = archive_source(source) if is_fully_done(document) else None
    if archived is not None:
        source_path(archived)
        if (policy.checkout / archived).exists():
            raise ValueError('todo_archive_path_conflict')
        document['status'] = 'done'
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()
    target = archived or source
    sealed = policy.root / 'sealed' / item['task_id'] / target
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_bytes(encoded)
    sealed.chmod(0o400)
    if archived is not None:
        # The completed document moves with this publication: the old path is removed from the
        # checkout and the same bytes are added under todos/archiv.
        item['hashes'].pop(source, None)
        removed = list(item.get('removed') or [])
        if source not in removed:
            removed.append(source)
        item['removed'] = sorted(removed)
    item['hashes'][target] = hashlib.sha256(encoded).hexdigest()


def archive_completed_sources(policy, *, publish) -> list[str]:
    """Deterministic bookkeeping: archive TODO documents that are already fully worked off.

    A document only completes through publication, so a document that was already done before
    this loop existed would otherwise stay in ``todos/active`` forever. This sweep needs no
    model: it moves exactly the fully done, not-yet-archived documents in one closed change
    set and returns the sources it moved.
    """
    from agent.services.self_development.catalog import iter_todo_documents
    from agent.services.self_development.workspace import git

    moves: dict[str, str] = {}
    for source, document in iter_todo_documents(policy.checkout):
        if not is_fully_done(document):
            continue
        archived = archive_source(source)
        if archived is None:
            continue
        if (policy.checkout / archived).exists():
            raise ValueError('todo_archive_path_conflict')
        moves[source] = archived
    if not moves:
        return []
    task_id = 'todo-archive-' + hashlib.sha256('\0'.join(sorted(moves)).encode()).hexdigest()[:16]
    seal = policy.root / 'sealed' / task_id
    seal.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for source, archived in sorted(moves.items()):
        content = read_source(policy.checkout, source)
        target = seal / archived
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o400)
        hashes[archived] = hashlib.sha256(content).hexdigest()
    item = {
        'task_id': task_id,
        'source': 'todos',
        'status': 'running',
        'contract': {
            'commit_message': f'chore(self-development): archive {len(moves)} completed todo document(s)',
        },
        'hashes': hashes,
        'removed': sorted(moves),
        'revision': git(policy.checkout, 'rev-parse', 'HEAD'),
    }
    publish(item)
    return sorted(moves)
