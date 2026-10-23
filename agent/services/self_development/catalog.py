"""Read tracked TODO sources, preserving status, identity and dependency facts."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final, TypeAlias, TypedDict

from agent.services.self_development.contracts import DONE, flatten_tasks, task_contract
from agent.services.self_development.workspace import git

JSONValue: TypeAlias = str | int | float | bool | None | list['JSONValue'] | dict[str, 'JSONValue']
MAX_REQUIREMENTS_BYTES: Final = 65536


class TaskRequirements(TypedDict):
    acceptance_criteria: JSONValue
    references: JSONValue
    type: JSONValue
    goal: JSONValue
    document_goal: JSONValue
    document_purpose: JSONValue


class InvalidTaskRequirements(ValueError):
    """Requirements cannot be forwarded intact inside the bounded assignment."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


def task_requirements(task: Mapping[str, JSONValue],
                      document: Mapping[str, JSONValue]) -> TaskRequirements:
    """Copy source requirements without treating metadata as execution authority."""
    requirements: TaskRequirements = {
        'acceptance_criteria': task.get('acceptance_criteria', []),
        'references': task.get('references', []),
        'type': task.get('type', ''),
        'goal': task.get('goal', ''),
        'document_goal': document.get('goal', ''),
        'document_purpose': document.get('purpose', ''),
    }
    try:
        encoded = json.dumps(requirements, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidTaskRequirements('invalid_task_requirements') from exc
    if len(encoded) > MAX_REQUIREMENTS_BYTES:
        raise InvalidTaskRequirements('task_requirements_too_large')
    return json.loads(encoded)


def is_catalog_source(name: str) -> bool:
    """A tracked TODO document outside the archives and not a schema file."""
    parts = Path(name).parts
    return not (any(p in {'archive', 'archiv'} for p in parts) or 'schema' in Path(name).name)


def iter_todo_documents(root: Path):
    """Yield ``(source, document)`` for every tracked, catalogued TODO document."""
    names = git(root, 'ls-files', 'todos/*.json', 'todo*.json').splitlines()
    for name in names:
        if not is_catalog_source(name):
            continue
        try:
            yield name, json.loads((root / name).read_text())
        except (ValueError, OSError):
            continue


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
        requirements = {}
        try:
            requirements = task_requirements(task, document)
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
            'contract': contract, 'source_execution': task.get('execution'),
            # An operator may pin the item to one worker (e.g. an A/B comparison
            # pair must not share a backend by dispatch luck).
            **({'assigned_agent_url': str(task['assigned_agent_url'])} if task.get('assigned_agent_url') else {}),
            **requirements})
    return result


def catalog(root: Path, policy):
    names = git(root, 'ls-files', 'todos/*.json', 'todo*.json').splitlines()
    records = []
    errors = []
    documents = 0
    for name in names:
        if not is_catalog_source(name):
            continue
        try:
            document = json.loads((root / name).read_text())
            records.extend(import_document(document, name, policy))
            documents += 1
        except (ValueError, OSError) as exc:
            errors.append({'source': name, 'reason': type(exc).__name__})
    return {'documents': documents, 'items': records, 'errors': errors}
