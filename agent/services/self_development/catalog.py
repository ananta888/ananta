"""Read tracked TODO sources, preserving status, identity and dependency facts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.self_development.contracts import DONE, flatten_tasks, task_contract
from agent.services.self_development.workspace import git


def import_document(document: dict, source: str, policy) -> list[dict]:
    tasks = list(flatten_tasks(document))
    done = {str(t['id']) for t in tasks if str(t.get('status', '')).lower() in DONE}
    result = []
    seen = set()
    for task in tasks:
        original = str(task.get('status', 'todo')).lower()
        if original in DONE:
            continue
        identifier = str(task['id'])
        if identifier in seen:
            raise ValueError('duplicate_todo_identifier:' + identifier)
        seen.add(identifier)
        key = hashlib.sha256((source + '\0' + identifier).encode()).hexdigest()[:32]
        reason = None
        contract = None
        try:
            contract = task_contract(task)
            policy.authorize_contract(contract)
        except ValueError as exc:
            reason = str(exc)
        if original in {'blocked', 'blocked_by_dependency', 'paused', 'needs_review'}:
            reason = 'source_task_' + original
        dependencies = task.get('depends_on', task.get('dependencies', [])) or []
        if not isinstance(dependencies, list) or not all(isinstance(dep, str) for dep in dependencies):
            reason = 'invalid_dependencies'
            dependencies = []
        result.append({'key': key, 'source': source, 'todo_id': identifier,
            'title': str(task.get('title') or task.get('name') or identifier)[:200],
            'description': str(task.get('description') or task.get('implementation_note') or ''),
            'original_status': original, 'status': 'blocked' if reason else 'ready',
            'reason': reason, 'dependencies': [d for d in dependencies if d not in done],
            'contract': contract, 'source_execution': task.get('execution')})
    return result


def catalog(root: Path, policy):
    names = git(root, 'ls-files', 'todos/*.json', 'todo*.json').splitlines()
    records = []
    errors = []
    documents = 0
    for name in names:
        if any(p in {'archive', 'archiv'} for p in Path(name).parts) or 'schema' in Path(name).name:
            continue
        try:
            document = json.loads((root / name).read_text())
            records.extend(import_document(document, name, policy))
            documents += 1
        except (ValueError, OSError) as exc:
            errors.append({'source': name, 'reason': type(exc).__name__})
    return {'documents': documents, 'items': records, 'errors': errors}
